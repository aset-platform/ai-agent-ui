# Sentiment Dormancy Tracking

Persistent per-ticker tracking of "this ticker returns no headlines" so the daily sentiment batch stops hammering Yahoo/Google for known-empty stocks. Mirrors the `ingestion_skipped` PG-table pattern.

## Why it exists

Pre-implementation, the sentiment job tried `_fetch_yfinance + _fetch_yahoo_rss + _fetch_google_rss` for every ticker in the top-50 learning set every day. Mid/small-cap Indian stocks (AHLUCONT, GRWRHITECH, AEGISLOG…) consistently returned 0 headlines. Three problems:

1. ~150 wasted HTTP calls per run (3 sources × 50 attempted)
2. Yahoo/Google rate-limited the entire batch above ~5 parallel workers
3. The freshness filter then silently dropped these tickers into Step-5 market_fallback every day

## PG schema

`backend/db/models/sentiment_dormant.py` — Alembic migration `a9c1b3d5e7f2`:

```
ticker                  VARCHAR(30) PRIMARY KEY
consecutive_empty       INT NOT NULL DEFAULT 0
last_checked_at         TIMESTAMPTZ NOT NULL
next_retry_at           TIMESTAMPTZ NULL
last_headline_count     INT NOT NULL DEFAULT 0
last_seen_headlines_at  TIMESTAMPTZ NULL
created_at / updated_at TIMESTAMPTZ
INDEX (next_retry_at)   -- the "ready to retry" query
```

## Cooldown schedule (capped exponential)

```python
_DORMANT_COOLDOWN_DAYS = (2, 4, 8, 16, 30)  # index = consecutive_empty - 1, cap at 30
```

| consecutive_empty | next_retry_at offset |
|---|---|
| 1 | +2 days  |
| 2 | +4 days  |
| 3 | +8 days  |
| 4 | +16 days |
| ≥5 | +30 days (cap) |

## PG access helpers (`backend/db/pg_stocks.py`)

- `get_dormant_tickers(session) -> set[str]` — tickers where `next_retry_at > now()`
- `get_dormant_eligible_for_probe(session, limit)` — ordered by oldest `last_checked_at`
- `record_empty_fetch(session, ticker)` — bumps `consecutive_empty`, sets `next_retry_at`
- `record_successful_fetch(session, ticker, headline_count)` — clears state on a non-empty fetch (no-op if no row exists — table only carries dormancy metadata)

## Wired into scorer (`backend/tools/_sentiment_scorer.py`)

In `refresh_ticker_sentiment`:
- Empty-headline path → `record_empty_fetch()` then return None
- Non-empty path → `record_successful_fetch()` BEFORE scoring (clears dormancy even if scoring fails downstream)
- Both wrapped in best-effort try/except so a PG hiccup never blocks scoring
- Uses the existing `_pg_session()` NullPool sync→async bridge from `stocks/repository.py`

## Wired into executor (`backend/jobs/executor.py::execute_run_sentiment`)

Step 2 classification:
- `dormant_tickers = get_dormant_tickers()` — single PG query upfront
- `dormant_skip` bucket: tickers whose retry window hasn't lifted (excluded from learning + cold)
- `dormant_probe` slice: 5% of in-scope dormant, sampled by oldest `last_checked_at`, included in trickle for periodic re-discovery
- `force=True` runs ignore dormancy (operator override)
- New classification log: `Classification: ... %d dormant (%d skip + %d probe)`

## Operational behaviour observed

Run 1 (fresh state): 53 tickers returned 0 headlines → 53 dormant rows created at `streak=1, next_retry +2 days`.

Run 2 (immediate): classification line shows `53 dormant (51 skip + 2 probe)`. 51 tickers skipped entirely (~150 HTTP calls saved). 2 probed by oldest-`last_checked_at`; both still empty → bumped to `streak=2`.

Steady state after ~5 runs: dormant pool stabilises around the 30-50% of universe with thin coverage; daily HTTP budget drops by ~60%.

## What it's NOT

- Not a global skip list — only per-ticker, with auto-recovery via the 5% probe.
- Not a permanent disable — the 30-day cap means even consistent-empty tickers get re-tested monthly.
- Not source-aware — granularity is "any source returned headlines" not per-source. Sufficient for current needs.

## See also

- `shared/architecture/finbert-sentiment-scoring` — the scorer pipeline this dormancy plugs into
- `shared/conventions/iceberg-freshness-checks` — why the freshness query on `sentiment_scores` filters `WHERE close IS NOT NULL AND NOT isnan(close)` (same pattern used here for `next_retry_at`)
- ASETPLTFRM-320 — sprint 7 sentiment hardening parent ticket
