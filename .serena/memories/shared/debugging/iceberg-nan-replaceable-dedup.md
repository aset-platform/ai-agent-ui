# Iceberg NaN-replaceable dedup pattern

Pattern: when an Iceberg table dedupes inserts on `(key1, key2)` only, a row with a NaN value column (e.g. `close=NaN` from a stuck Yahoo upstream) will FOREVER block any future re-fetch from replacing it — the dedup sees `(key1, key2)` as "already present" and drops the new valid row as a duplicate.

## Where it bit us

`stocks.ohlcv`. Two write paths:
- `stocks/repository.py::insert_ohlcv` — per-ticker insert
- `backend/jobs/batch_refresh.py::batch_data_refresh` — bulk insert

Both queried existing dates as `SELECT date FROM ohlcv WHERE ticker = ?` and rejected any incoming date already in the result set. When Yahoo returned 04-22 with `Close=NaN` for 29 ETFs, the rows landed in Iceberg. When Yahoo settled the actual close hours later, the next pipeline run silently dropped the corrected value as "duplicate" — the dedup didn't care that the existing row's close was NaN.

## The fix (applied in both paths)

Two-part change:

### Part 1: filter the dedup query to non-NaN rows

```python
# OLD
edf = query_iceberg_df(_OHLCV,
    "SELECT date FROM ohlcv WHERE ticker = ?", [ticker])

# NEW
edf = query_iceberg_df(_OHLCV,
    "SELECT date FROM ohlcv WHERE ticker = ?"
    " AND close IS NOT NULL AND NOT isnan(close)", [ticker])
```

For PyIceberg fallback path (used when DuckDB unavailable):

```python
from pyiceberg.expressions import And, EqualTo, NotNaN, NotNull
existing = tbl.scan(
    row_filter=And(
        EqualTo("ticker", ticker),
        NotNull("close"),
        NotNaN("close"),
    ),
    selected_fields=("date",),
).to_arrow()
```

### Part 2: scoped pre-delete of NaN rows for to-be-inserted dates

Without this, you'd insert the new valid row alongside the existing NaN row → two rows for the same `(ticker, date)` → chart duplicate-timestamp assertion.

```python
from pyiceberg.expressions import And, EqualTo, In, IsNaN, IsNull, Or
self._delete_rows(
    _OHLCV,
    And(
        EqualTo("ticker", ticker),
        In("date", new_dates),
        Or(IsNull("close"), IsNaN("close")),
    ),
)
self._append_rows(_OHLCV, arrow_tbl)
```

No-op when there are no NaN rows for the target dates (common case).

## Verified

```python
# Pre-fix
df = pd.DataFrame({"Close": [275.5], ...}, index=pd.DatetimeIndex(["2026-04-22"]))
n = repo.insert_ohlcv("NIFTYBEES.NS", df)
# n=0 (NaN row blocks)

# Post-fix
n = repo.insert_ohlcv("NIFTYBEES.NS", df)
# n=1, exactly one row remains for (NIFTYBEES.NS, 2026-04-22)
```

Idempotency on clean tickers preserved (re-insert returns 0, no-op).

## Generalisable

Same pattern applies to ANY per-(ticker, date) Iceberg upsert. If you're writing a similar table:
- Choose a "validity" column (`close`, `avg_score`, etc.) that the freshness query already cares about
- Filter the dedup query to non-NaN/non-null rows on that column
- Add a scoped pre-delete of NaN rows for the to-be-inserted key set before append

The freshness query in `batch_refresh.py:495` already does `WHERE close IS NOT NULL AND NOT isnan(close)` for the latest-date computation — same pattern, now mirrored in the dedup.

## Related gotcha

`shared/debugging/ohlcv-nan-close-price` — explains the upstream NaN issue (Yahoo's NSE feed) that necessitated this fix. The `Clean NaN Rows` admin button (`backfill_nan` in `routes.py`) is the manual escape hatch; this fix means it's only needed for permanent gap days where Yahoo never publishes a close at all.
