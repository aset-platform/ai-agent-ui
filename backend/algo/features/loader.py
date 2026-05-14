"""Partition-chunk Redis + Iceberg loader for
``stocks.intraday_features`` (ASETPLTFRM-402 / FE-4).

The intraday backtest runner reads its per-bar feature panel
exclusively through :func:`load_intraday_features_window` —
slice-4b's in-memory ``compute_indicators_for_universe_intraday``
is deleted in this same slice. Per spec §7.3, NO in-memory
fallback exists: when the feature store is empty for a
requested ``(ticker, year_month, interval_sec)`` chunk, the
loader triggers an on-demand backfill via
:func:`backend.algo.features.backfill.backfill_features_window`
and re-reads. If a chunk is still empty after that, a
:class:`FeaturePanelMissingError` is raised with the exact
missing tuples — the runner surfaces this fail-fast rather
than masking missing data with a silent compute.

Cache layout (per spec §7.4): keys are STRATEGY-AGNOSTIC and
keyed by ``(ticker, year_month, interval_sec)`` so a single
feature-store write invalidates the same key every strategy
reads. TTL = ``TTL_STABLE`` (300s) — feature rows change at
most once per minute (the live cadence) and a 5-minute upper
bound on staleness is well below any latency-sensitive UX.

Cache invalidation hook lives in the feature writers (FE-3's
daily-compute job and the on-demand
``backfill_features_window``) — both call
``cache.invalidate("cache:feature:chunk:*:{year_month}:*")``
on success so the next loader read after a write picks up
fresh data.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from pyiceberg.expressions import And, EqualTo, In

from backend.algo.features.version import FEATURE_SET_VERSION
from backend.cache import TTL_STABLE, get_cache

_logger = logging.getLogger(__name__)

_FEATURES_TABLE = "stocks.intraday_features"

# Features whose values are stored as Decimal-encoded numerics
# in the panel. Everything not in this set is treated as
# ``string`` on read — only ``time_of_day_bucket`` qualifies
# in Phase 1 but the design admits future string features
# without code change.
_STRING_FEATURES: frozenset[str] = frozenset({"time_of_day_bucket"})

# Mapping {numeric feature_value} → label. Persisted layer
# stores ``time_of_day_bucket`` as Doubles because the Iceberg
# schema constrains ``feature_value`` to DoubleType. The
# writer (FE-3 ``_panel_to_arrow_rows``) skips str features
# entirely, so on the read side ``time_of_day_bucket`` will be
# absent — strategies that key on it must compute from
# ``minutes_since_open`` instead. Documented here so the next
# reader knows the contract.


class FeaturePanelMissingError(RuntimeError):
    """Raised when one or more requested ``(ticker, year_month,
    interval_sec)`` chunks have no rows in Iceberg even after
    on-demand backfill.

    The exception message lists the exact missing tuples so the
    runner caller can surface a precise error to the user
    (e.g. "request a wider warmup" or "ticker not yet ingested").
    """


def _chunk_key(
    ticker: str,
    year_month: str,
    interval_sec: int,
) -> str:
    """Redis key for a single ``(ticker, year_month, interval_sec)``
    feature chunk. Strategy-agnostic per spec §7.4.
    """
    return f"cache:feature:chunk:{ticker}:{year_month}:{interval_sec}"


def _year_months_in_window(
    period_start: date,
    period_end: date,
) -> list[str]:
    """Return the sorted list of ``YYYY-MM`` strings covering
    every month touched by the inclusive window
    ``[period_start, period_end]``.
    """
    out: list[str] = []
    cur = date(period_start.year, period_start.month, 1)
    end_marker = date(period_end.year, period_end.month, 1)
    while cur <= end_marker:
        out.append(f"{cur.year:04d}-{cur.month:02d}")
        # Advance one month — handle December rollover explicitly.
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def _decompose_into_chunks(
    tickers: Iterable[str],
    period_start: date,
    period_end: date,
) -> list[tuple[str, str]]:
    """Cartesian product of (ticker, year_month) chunks covering
    the window. Order is deterministic so the loader's
    behaviour is stable across runs.
    """
    months = _year_months_in_window(period_start, period_end)
    return [(t, ym) for t in tickers for ym in months]


def _serialize_chunk(rows: list[dict[str, Any]]) -> str:
    """JSON-serialize a list of feature rows for the cache.

    msgpack would be marginally faster but is not currently a
    dependency. JSON is sufficient — a typical 15m chunk is
    ~250 rows × ~26 features ≈ 6.5k feature triples; serialized
    payload is ~150-300 KB which Redis handles trivially.
    Decimals are coerced to strings to preserve exact precision
    (``json.dumps`` rejects Decimal natively).
    """

    def _enc(v: Any) -> Any:
        if isinstance(v, Decimal):
            return str(v)
        return v

    return json.dumps(
        [{k: _enc(v) for k, v in r.items()} for r in rows],
        separators=(",", ":"),
    )


def _deserialize_chunk(blob: str) -> list[dict[str, Any]]:
    """Inverse of :func:`_serialize_chunk`. ``feature_value`` is
    coerced back to ``Decimal`` for numeric features; the bar
    timestamp stays an int.
    """
    raw = json.loads(blob)
    out: list[dict[str, Any]] = []
    for r in raw:
        feat_name = r.get("feature_name")
        fv = r.get("feature_value")
        if feat_name in _STRING_FEATURES:
            # Phase-1: no string features are persisted (see
            # module docstring); branch reserved for FE-5+.
            r["feature_value"] = str(fv) if fv is not None else None
        else:
            r["feature_value"] = Decimal(str(fv)) if fv is not None else None
        r["bar_open_ts_ns"] = int(r["bar_open_ts_ns"])
        out.append(r)
    return out


def _scan_iceberg_for_chunks(
    *,
    tickers: list[str],
    year_months: list[str],
    interval_sec: int,
    feature_set_version: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Single Iceberg scan filtered on the union of missing
    ``(ticker, year_month)`` chunks. Result is grouped by
    ``(ticker, year_month)`` so the caller can populate the
    cache one chunk at a time.

    Empty / missing-table / commit-race failures fall through
    to ``{}`` (caller treats every chunk as still-empty and
    falls into the on-demand-backfill path).
    """
    if not tickers or not year_months:
        return {}
    from stocks.create_tables import _INTRADAY_FEATURES_TABLE, _get_catalog

    try:
        cat = _get_catalog()
        tbl = cat.load_table(_INTRADAY_FEATURES_TABLE)
        # ``.refresh()`` ensures we see committed rows from any
        # writer that fired between cache-miss and this scan
        # (see CLAUDE.md §5.1 — DuckDB read-after-write hazard).
        tbl = tbl.refresh()
        row_filter = And(
            And(
                In("ticker", tickers),
                In("year_month", year_months),
            ),
            And(
                EqualTo("interval_sec", int(interval_sec)),
                EqualTo(
                    "feature_set_version",
                    feature_set_version,
                ),
            ),
        )
        df = tbl.scan(row_filter=row_filter).to_pandas()
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[features-loader] Iceberg scan failed "
            "(tickers=%d, months=%d, interval_sec=%d): %s",
            len(tickers),
            len(year_months),
            interval_sec,
            exc,
            exc_info=True,
        )
        return {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if df is None or df.empty:
        return grouped
    for rec in df.to_dict(orient="records"):
        key = (str(rec["ticker"]), str(rec["year_month"]))
        grouped.setdefault(key, []).append(
            {
                "ticker": str(rec["ticker"]),
                "bar_open_ts_ns": int(rec["bar_open_ts_ns"]),
                "bar_date": str(rec["bar_date"]),
                "year_month": str(rec["year_month"]),
                "interval_sec": int(rec["interval_sec"]),
                "feature_name": str(rec["feature_name"]),
                "feature_value": rec["feature_value"],
                "feature_set_version": str(rec["feature_set_version"]),
            }
        )
    return grouped


def _ym_to_window(year_month: str) -> tuple[date, date]:
    """``YYYY-MM`` → ``(first_of_month, last_of_month)`` inclusive."""
    year, month = year_month.split("-")
    y = int(year)
    m = int(month)
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last_day)


def _missing_months_window(
    missing_chunks: list[tuple[str, str]],
) -> tuple[date, date] | None:
    """Combined ``[min(start), max(end)]`` window covering every
    missing year_month. Used as the backfill payload — the
    backfill job's own scan filters per-ticker so over-fetching
    months on the date axis is cheap.
    """
    if not missing_chunks:
        return None
    months = sorted({ym for (_t, ym) in missing_chunks})
    s, _ = _ym_to_window(months[0])
    _, e = _ym_to_window(months[-1])
    return s, e


def _run_backfill_sync(
    *,
    tickers: list[str],
    interval_sec: int,
    period_start: date,
    period_end: date,
    feature_set_version: str,
) -> None:
    """Drive the async ``backfill_features_window`` from a sync
    caller.

    The loader is invoked from the synchronous backtest runner
    (see CLAUDE.md §6.7 ``sync-async-migration-patterns``). The
    backfill helper is async because it nests
    ``disposable_pg_session()`` for universe resolution; we
    bridge with ``asyncio.run`` — safe here because the runner
    has no surrounding event loop. If a caller invokes us from
    INSIDE an event loop (e.g. async test), ``asyncio.run``
    would raise ``RuntimeError``; in that case we surface a
    clear error rather than guess at thread-pool dispatch.
    """
    from backend.algo.features.backfill import backfill_features_window

    async def _run() -> dict[str, Any]:
        return await backfill_features_window(
            tickers=tickers,
            interval_sec=interval_sec,
            period_start=period_start,
            period_end=period_end,
            feature_set_version=feature_set_version,
        )

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError(
            "load_intraday_features_window invoked from inside "
            "a running event loop; on-demand backfill needs a "
            "fresh loop. Call the loader from a sync thread "
            "(e.g. asyncio.to_thread) or pre-populate the "
            "feature store before triggering the runner."
        )
    asyncio.run(_run())


def load_intraday_features_window(
    tickers: list[str],
    interval_sec: int,
    period_start: date,
    period_end: date,
    *,
    feature_set_version: str = FEATURE_SET_VERSION,
    enable_on_demand_backfill: bool = True,
) -> dict[str, dict[int, dict[str, Decimal | str]]]:
    """Return per-bar feature panel for the requested universe.

    Algorithm (per spec §7.4):
      1. Decompose ``(tickers, period_start, period_end)`` into
         ``(ticker, year_month)`` chunks.
      2. Fetch each chunk's blob from Redis sequentially.
      3. Single Iceberg scan for chunks still missing after the
         Redis pass; group rows by chunk and write each back to
         Redis with ``TTL_STABLE``.
      4. If chunks remain empty AND
         ``enable_on_demand_backfill`` is True, call
         :func:`backfill_features_window` over the combined
         missing-month window for the missing tickers, then
         re-scan Iceberg + repopulate Redis.
      5. Slice every assembled panel to bars whose IST date is
         within ``[period_start, period_end]``.
      6. If any chunk is STILL empty, raise
         :class:`FeaturePanelMissingError` listing every
         missing ``(ticker, year_month, interval_sec)``.

    Args:
        tickers: Symbols to load. Order is preserved in the
            output dict's key set when possible.
        interval_sec: Bar cadence — one of ``(900, 300, 60)``.
        period_start, period_end: Inclusive ISO window bounds.
            The function loads at year_month granularity and
            slices to the per-day bounds at the end.
        feature_set_version: Stamped onto every persisted row;
            used as a filter component when reading. Bumping it
            forces a fresh read path (old rows ignored).
        enable_on_demand_backfill: Set False in tests / CLI
            dry-runs that don't want the loader to commit
            anything to Iceberg even on miss.

    Returns:
        Nested panel
        ``{ticker: {bar_open_ts_ns: {feature_name: Decimal | str}}}``.
        Feature values are ``Decimal`` for every numeric feature
        and ``str`` for the (Phase-2) ``time_of_day_bucket``.
    """
    cache = get_cache()
    chunks = _decompose_into_chunks(
        tickers,
        period_start,
        period_end,
    )
    if not chunks:
        return {}

    # ── Phase 1: Redis pass ────────────────────────────────────
    panel_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    missing_chunks: list[tuple[str, str]] = []
    for ticker, ym in chunks:
        key = _chunk_key(ticker, ym, interval_sec)
        try:
            blob = cache.get(key)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[features-loader] cache.get crashed " "key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            blob = None
        if blob is None:
            missing_chunks.append((ticker, ym))
            continue
        try:
            panel_rows[(ticker, ym)] = _deserialize_chunk(blob)
        except Exception as exc:  # noqa: BLE001
            # Corrupt blob — drop and treat as miss. Don't
            # delete the key, the next write will overwrite.
            _logger.warning(
                "[features-loader] cache blob deserialize "
                "failed key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            missing_chunks.append((ticker, ym))

    # ── Phase 2: Iceberg backfill for misses ──────────────────
    if missing_chunks:
        missing_tickers = sorted({t for (t, _ym) in missing_chunks})
        missing_months = sorted({ym for (_t, ym) in missing_chunks})
        grouped = _scan_iceberg_for_chunks(
            tickers=missing_tickers,
            year_months=missing_months,
            interval_sec=interval_sec,
            feature_set_version=feature_set_version,
        )
        for (t, ym), rows in grouped.items():
            panel_rows[(t, ym)] = rows
            # Write-through to cache.
            try:
                cache.set(
                    _chunk_key(t, ym, interval_sec),
                    _serialize_chunk(rows),
                    ttl=TTL_STABLE,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "[features-loader] cache.set crashed "
                    "ticker=%s ym=%s: %s",
                    t,
                    ym,
                    exc,
                    exc_info=True,
                )

    # ── Phase 3: on-demand backfill for STILL-missing chunks ──
    still_missing = [c for c in missing_chunks if c not in panel_rows]
    if still_missing and enable_on_demand_backfill:
        backfill_tickers = sorted(
            {t for (t, _ym) in still_missing},
        )
        window = _missing_months_window(still_missing)
        if window is not None:
            bf_start, bf_end = window
            _logger.info(
                "[features-loader] on-demand backfill "
                "tickers=%d interval_sec=%d window=%s..%s",
                len(backfill_tickers),
                interval_sec,
                bf_start.isoformat(),
                bf_end.isoformat(),
            )
            try:
                _run_backfill_sync(
                    tickers=backfill_tickers,
                    interval_sec=interval_sec,
                    period_start=bf_start,
                    period_end=bf_end,
                    feature_set_version=feature_set_version,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "[features-loader] on-demand backfill " "crashed: %s",
                    exc,
                    exc_info=True,
                )
            # Re-scan only the still-missing chunks.
            grouped = _scan_iceberg_for_chunks(
                tickers=backfill_tickers,
                year_months=sorted(
                    {ym for (_t, ym) in still_missing},
                ),
                interval_sec=interval_sec,
                feature_set_version=feature_set_version,
            )
            for (t, ym), rows in grouped.items():
                panel_rows[(t, ym)] = rows
                try:
                    cache.set(
                        _chunk_key(t, ym, interval_sec),
                        _serialize_chunk(rows),
                        ttl=TTL_STABLE,
                    )
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "[features-loader] cache.set crashed "
                        "after backfill ticker=%s ym=%s: %s",
                        t,
                        ym,
                        exc,
                        exc_info=True,
                    )

    # ── Phase 4: fail-fast on STILL-missing chunks ────────────
    final_missing = [c for c in missing_chunks if c not in panel_rows]
    if final_missing:
        sample = ", ".join(
            f"({t}, {ym}, {interval_sec})" for (t, ym) in final_missing[:10]
        )
        raise FeaturePanelMissingError(
            f"feature panel missing for {len(final_missing)} "
            f"chunk(s) after backfill: {sample}"
            + ("..." if len(final_missing) > 10 else "")
        )

    # ── Phase 5: assemble + slice by IST date ─────────────────
    period_start_ns = _ist_day_ns(period_start, end_of_day=False)
    period_end_ns = _ist_day_ns(period_end, end_of_day=True)

    out: dict[str, dict[int, dict[str, Decimal | str]]] = {}
    for (ticker, _ym), rows in panel_rows.items():
        ticker_panel = out.setdefault(ticker, {})
        for r in rows:
            ts_ns = int(r["bar_open_ts_ns"])
            if ts_ns < period_start_ns or ts_ns > period_end_ns:
                continue
            feat_name = str(r["feature_name"])
            raw_val = r["feature_value"]
            # Iceberg rows arrive as raw floats; cache rows are
            # already Decimal-coerced by _deserialize_chunk.
            # Unify here so the runner sees Decimals uniformly
            # regardless of read path.
            if isinstance(raw_val, Decimal) or (feat_name in _STRING_FEATURES):
                value: Decimal | str = raw_val
            elif raw_val is None:
                continue
            else:
                value = Decimal(str(raw_val))
            feat_map = ticker_panel.setdefault(ts_ns, {})
            feat_map[feat_name] = value
    return out


def _ist_day_ns(d: date, *, end_of_day: bool) -> int:
    """``date`` → bar_open_ts_ns boundary for an IST trading day.

    ``end_of_day=False`` → 00:00:00 IST of ``d`` (inclusive
    lower bound). ``end_of_day=True`` → 23:59:59 IST of ``d``
    (inclusive upper bound). Used to slice the panel against
    the inclusive ``[period_start, period_end]`` window.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    if end_of_day:
        dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=ist)
    else:
        dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=ist)
    return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)
