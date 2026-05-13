"""Historical intraday bar backfill for ``stocks.intraday_bars``
(ASETPLTFRM-400 slice 1c).

Two entry points:

1. ``backfill_window`` — bulk pull a wide ``[start, end]`` window for
   a list of tickers via ``KiteClient.fetch_intraday_historical_window``
   (slice 1a), then bulk-upsert in batches. The CLI under
   ``if __name__ == '__main__'`` wraps this for the 4-year initial
   pull described in the epic.

2. ``ensure_window_present`` — on-demand path used by the intraday
   backtest runner (slice 2). Queries the existing coverage for the
   requested ticker/interval/window and pulls only the dates that
   are missing.

Both write through ``upsert_intraday_bars`` which uses the same
NaN-replaceable upsert pattern as ``backend.algo.factors.repo``
(scoped pre-delete by ``(ticker, bar_date, interval_sec)`` then
append). Idempotent — re-running over an already-populated window
overwrites with fresh data instead of duplicating rows.

CLI usage::

    PYTHONPATH=.:backend python -m backend.algo.backtest.intraday_backfill \\
        --interval 15m \\
        --start 2022-05-13 \\
        --end   2026-05-13 \\
        --universe india_full \\
        --user-id <uuid> \\
        --batch-size 50
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pyarrow as pa
from pyiceberg.expressions import And, EqualTo, In

from backend.algo.backtest.types import BarData
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)

_logger = logging.getLogger(__name__)

INTRADAY_BARS_TABLE = "stocks.intraday_bars"

# Supported intraday cadences keyed by interval_sec. Must agree with
# ``KiteClient._INTRADAY_INTERVAL_MAP`` — kept duplicated here so the
# CLI argparse can reject unknown ``--interval`` values before any
# Kite import.
_INTERVAL_ALIASES: dict[str, int] = {
    "1m": 60,
    "60s": 60,
    "5m": 300,
    "300s": 300,
    "15m": 900,
    "900s": 900,
}


@dataclass
class BackfillStats:
    """Aggregate result of one ``backfill_window`` invocation."""

    tickers_done: int = 0
    tickers_failed: int = 0
    bars_written: int = 0
    wall_clock_s: float = 0.0
    failures: list[tuple[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []


def _arrow_schema() -> pa.Schema:
    """Arrow schema for ``stocks.intraday_bars``.

    Every column is ``nullable=False`` to match the Iceberg schema's
    ``required=True`` — the factors repo lesson: PyArrow defaults to
    nullable=True which PyIceberg rejects on append against a
    required-column schema.
    """
    return pa.schema(
        [
            pa.field("ticker", pa.string(), nullable=False),
            pa.field("bar_date", pa.string(), nullable=False),
            pa.field("interval_sec", pa.int64(), nullable=False),
            pa.field("bar_open_ts_ns", pa.int64(), nullable=False),
            pa.field("open", pa.float64(), nullable=False),
            pa.field("high", pa.float64(), nullable=False),
            pa.field("low", pa.float64(), nullable=False),
            pa.field("close", pa.float64(), nullable=False),
            pa.field("volume", pa.int64(), nullable=False),
            pa.field("written_at", pa.timestamp("us"), nullable=False),
            pa.field("source", pa.string(), nullable=False),
        ]
    )


def _bars_to_arrow(
    bars: list[BarData],
    interval_sec: int,
    source: str,
) -> pa.Table:
    """Convert ``BarData`` list to the Arrow table written to
    ``stocks.intraday_bars``.

    Rejects bars that fail any of the required-column invariants
    (NaN OHLC, missing ``bar_open_ts_ns``). The slice-1e pipeline
    quality gates will catch these too, but failing here keeps the
    write atomic — partial Arrow tables are an Iceberg footgun.
    """
    if not bars:
        return pa.table({}, schema=_arrow_schema())

    # Iceberg ``TimestampType`` is tz-naive — strip tzinfo after
    # taking the UTC snapshot. See CLAUDE.md §5.1 +
    # iceberg-tz-naive-timestamps memory.
    written_at = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    rows: list[dict[str, Any]] = []
    for b in bars:
        if b.bar_open_ts_ns is None:
            continue
        try:
            o, h, lo, c = (
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
            )
        except (TypeError, ValueError):
            continue
        if any(math.isnan(x) for x in (o, h, lo, c)):
            continue
        rows.append(
            {
                "ticker": b.ticker,
                "bar_date": b.date.strftime("%Y-%m-%d"),
                "interval_sec": interval_sec,
                "bar_open_ts_ns": int(b.bar_open_ts_ns),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": int(b.volume),
                "written_at": written_at,
                "source": source,
            }
        )
    if not rows:
        return pa.table({}, schema=_arrow_schema())
    cols = {k: [r[k] for r in rows] for k in _arrow_schema().names}
    return pa.table(cols, schema=_arrow_schema())


def upsert_intraday_bars(
    bars: list[BarData],
    *,
    interval_sec: int,
    source: str = "kite",
) -> int:
    """NaN-replaceable upsert keyed on
    ``(ticker, bar_date, interval_sec)``.

    Pre-deletes the cross-product of incoming tickers × bar_dates at
    the given ``interval_sec`` then appends. Re-running the same
    window overwrites cleanly. The ``interval_sec`` term in the
    scoped delete prevents a 15m backfill from wiping a 5m table
    state for the same dates — critical for the on-demand path where
    multiple cadences coexist.

    Returns the row count actually written (after NaN/missing-ts
    filtering); zero is a valid no-op when ``bars`` is empty or all
    rows fail the required-column gate.
    """
    if not bars:
        return 0
    from backend.algo._iceberg_retry import retry_iceberg_op

    arrow_tbl = _bars_to_arrow(bars, interval_sec, source)
    if arrow_tbl.num_rows == 0:
        return 0

    tickers = sorted({b.ticker for b in bars if b.bar_open_ts_ns})
    bar_dates = sorted(
        {b.date.strftime("%Y-%m-%d") for b in bars if b.bar_open_ts_ns}
    )

    def _do_upsert() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(INTRADAY_BARS_TABLE)
        try:
            tbl.delete(
                And(
                    In("ticker", tickers),
                    In("bar_date", bar_dates),
                    EqualTo("interval_sec", interval_sec),
                ),
            )
        except Exception as exc:  # first run / empty partition
            _logger.debug(
                "intraday_bars pre-delete skipped (%s): %s",
                INTRADAY_BARS_TABLE,
                exc,
            )
        tbl.append(arrow_tbl)

    retry_iceberg_op(INTRADAY_BARS_TABLE, _do_upsert)
    invalidate_metadata(INTRADAY_BARS_TABLE)
    return arrow_tbl.num_rows


def get_existing_bar_dates(
    *,
    ticker: str,
    interval_sec: int,
    start: date,
    end: date,
) -> set[date]:
    """Return the set of ``bar_date`` (as ``date``) already covered
    for ``(ticker, interval_sec)`` in ``[start, end]``.

    Used by ``ensure_window_present`` to compute the missing-date
    sub-windows so the on-demand pull only hits Kite for what's
    actually missing.
    """
    rows = query_iceberg_table(
        INTRADAY_BARS_TABLE,
        "SELECT DISTINCT bar_date FROM intraday_bars "
        "WHERE ticker = ? AND interval_sec = ? "
        "  AND bar_date BETWEEN ? AND ?",
        [
            ticker,
            interval_sec,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        ],
    )
    out: set[date] = set()
    for r in rows or []:
        raw = r["bar_date"]
        if isinstance(raw, date):
            out.add(raw)
        else:
            try:
                out.add(date.fromisoformat(str(raw)[:10]))
            except (TypeError, ValueError):
                continue
    return out


def _date_range(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _missing_window(
    *,
    ticker: str,
    interval_sec: int,
    start: date,
    end: date,
) -> tuple[date, date] | None:
    """Return the smallest ``[missing_start, missing_end]`` covering
    every gap in ``[start, end]`` for ``(ticker, interval_sec)``.

    A single bounding window is cheaper for the backfill than
    issuing one Kite call per gap; the upsert will handle the
    re-fetched already-present days idempotently (the cost is paid
    in Kite calls, not in storage).
    """
    have = get_existing_bar_dates(
        ticker=ticker,
        interval_sec=interval_sec,
        start=start,
        end=end,
    )
    if not have:
        return start, end
    missing = [d for d in _date_range(start, end) if d not in have]
    if not missing:
        return None
    return missing[0], missing[-1]


def ensure_window_present(
    *,
    kite: Any,
    ticker: str,
    instrument_token: int,
    interval_sec: int,
    start: date,
    end: date,
    source: str = "kite_on_demand",
) -> int:
    """On-demand backfill path used by the intraday backtest runner.

    Computes the bounding window of missing dates and pulls just
    that span via ``fetch_intraday_historical_window``. No-op
    (returns 0) when coverage is already complete.

    Returns the bar count written.
    """
    window = _missing_window(
        ticker=ticker,
        interval_sec=interval_sec,
        start=start,
        end=end,
    )
    if window is None:
        return 0
    missing_start, missing_end = window
    bars = kite.fetch_intraday_historical_window(
        ticker=ticker,
        instrument_token=instrument_token,
        interval_sec=interval_sec,
        start=missing_start,
        end=missing_end,
    )
    return upsert_intraday_bars(
        bars,
        interval_sec=interval_sec,
        source=source,
    )


def backfill_window(
    *,
    kite: Any,
    tickers: list[str],
    instrument_tokens: dict[str, int],
    interval_sec: int,
    start: date,
    end: date,
    source: str = "manual_backfill",
    batch_size: int = 50,
    on_batch_written: Any = None,
) -> BackfillStats:
    """Bulk pull ``[start, end]`` for ``tickers`` at ``interval_sec``
    and write to ``stocks.intraday_bars``.

    Tickers are grouped into batches of ``batch_size``; each batch
    is fetched serially (the class-level 3 req/s throttle on
    ``KiteClient._hist_throttle`` enforces the rate cap) and
    upserted in one Iceberg commit per batch. Per-ticker failures
    are logged with ``exc_info=True`` (CLAUDE.md hard rule #10) and
    the run continues — one bad ticker does not strand the rest.

    Parameters
    ----------
    kite : KiteClient
        Pre-authenticated client. Must have an access_token set.
    tickers : list[str]
        Ticker symbols (e.g. ``["ITC.NS", "RELIANCE.NS"]``).
    instrument_tokens : dict[str, int]
        Map from each ticker → Kite numeric instrument_token.
        Tickers missing from this map are recorded as failures.
    interval_sec : int
        60 (1m), 300 (5m), or 900 (15m).
    start, end : date
        Inclusive window bounds.
    source : str
        Stored verbatim in the ``source`` column of every written
        bar. ``manual_backfill`` for the CLI, ``kite_on_demand`` for
        the on-demand path, etc.
    batch_size : int
        Number of tickers per upsert commit. Default 50 keeps each
        scoped-delete batch reasonable.
    on_batch_written : Callable[[list[BarData], int], None] | None
        Optional hook invoked after each successful batch upsert
        with ``(batch_bars, interval_sec)``. Used by the daily
        keeper (ASETPLTFRM-400 slice 1e) to run the pipeline
        quality assertions and emit ``data_quality_violation``
        events. Hook exceptions are caught + logged with
        ``exc_info=True`` so an assertion bug never strands the
        keeper.

    Returns
    -------
    BackfillStats
        Aggregate counters + per-ticker failures.
    """
    stats = BackfillStats()
    t0 = time.monotonic()
    batches = [
        tickers[i : i + batch_size] for i in range(0, len(tickers), batch_size)
    ]
    total = len(tickers)
    _logger.info(
        "[intraday-backfill] start tickers=%d batches=%d "
        "interval_sec=%d start=%s end=%s source=%s",
        total,
        len(batches),
        interval_sec,
        start,
        end,
        source,
    )
    for bi, batch in enumerate(batches, start=1):
        batch_bars: list[BarData] = []
        for tk in batch:
            token = instrument_tokens.get(tk)
            if token is None:
                _logger.warning(
                    "[intraday-backfill] %s missing instrument "
                    "token — skipping",
                    tk,
                )
                stats.tickers_failed += 1
                stats.failures.append((tk, "missing_token"))
                continue
            try:
                bars = kite.fetch_intraday_historical_window(
                    ticker=tk,
                    instrument_token=token,
                    interval_sec=interval_sec,
                    start=start,
                    end=end,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "[intraday-backfill] %s fetch failed: %s",
                    tk,
                    exc,
                    exc_info=True,
                )
                stats.tickers_failed += 1
                stats.failures.append((tk, f"fetch:{exc!s}"[:200]))
                continue
            batch_bars.extend(bars)
            stats.tickers_done += 1
        if batch_bars:
            try:
                written = upsert_intraday_bars(
                    batch_bars,
                    interval_sec=interval_sec,
                    source=source,
                )
                stats.bars_written += written
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "[intraday-backfill] batch %d/%d upsert " "failed: %s",
                    bi,
                    len(batches),
                    exc,
                    exc_info=True,
                )
                # Roll back the batch's tickers_done so the count
                # reflects actual writes, then re-record each as a
                # failure tagged with the upsert error.
                for tk in batch:
                    if tk in instrument_tokens:
                        stats.tickers_done -= 1
                        stats.tickers_failed += 1
                        stats.failures.append(
                            (tk, f"upsert:{exc!s}"[:200]),
                        )
            else:
                if on_batch_written is not None:
                    try:
                        on_batch_written(batch_bars, interval_sec)
                    except Exception as hook_exc:  # noqa: BLE001
                        _logger.error(
                            "[intraday-backfill] batch %d/%d "
                            "on_batch_written hook failed: %s",
                            bi,
                            len(batches),
                            hook_exc,
                            exc_info=True,
                        )
        _logger.info(
            "[intraday-backfill] batch %d/%d done "
            "(running total: %d done, %d failed, %d bars)",
            bi,
            len(batches),
            stats.tickers_done,
            stats.tickers_failed,
            stats.bars_written,
        )
    stats.wall_clock_s = time.monotonic() - t0
    _logger.info(
        "[intraday-backfill] complete tickers_done=%d "
        "tickers_failed=%d bars_written=%d elapsed=%.1fs",
        stats.tickers_done,
        stats.tickers_failed,
        stats.bars_written,
        stats.wall_clock_s,
    )
    return stats


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────


def _parse_interval(raw: str) -> int:
    """Map ``--interval`` argument to ``interval_sec``.

    Accepts ``1m`` / ``5m`` / ``15m`` (preferred) or raw seconds
    (``60`` / ``300`` / ``900``). Any other value raises
    ``argparse.ArgumentTypeError`` so the CLI errors before any
    Kite import.
    """
    raw_low = raw.strip().lower()
    if raw_low in _INTERVAL_ALIASES:
        return _INTERVAL_ALIASES[raw_low]
    try:
        as_int = int(raw_low)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--interval={raw!r} not recognised; use one of "
            f"{sorted(_INTERVAL_ALIASES)} or 60/300/900",
        ) from exc
    if as_int not in (60, 300, 900):
        raise argparse.ArgumentTypeError(
            f"--interval={as_int}s not supported; use 60 / 300 / 900",
        )
    return as_int


def _parse_iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"date {raw!r} is not ISO YYYY-MM-DD",
        ) from exc


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="intraday_backfill",
        description=(
            "Backfill stocks.intraday_bars from Kite "
            "(ASETPLTFRM-400 slice 1c)."
        ),
    )
    p.add_argument("--interval", type=_parse_interval, required=True)
    p.add_argument("--start", type=_parse_iso_date, required=True)
    p.add_argument("--end", type=_parse_iso_date, required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--universe",
        choices=["india_full", "india_top200", "nifty500"],
        help=(
            "Ticker source. 'nifty500' reads "
            "data/universe/nifty500.csv (same source the daily "
            "keeper uses). 'india_full' / 'india_top200' read "
            "stocks.universe_snapshot (latest rebalance)."
        ),
    )
    g.add_argument(
        "--tickers",
        help="Comma-separated ticker list (overrides --universe).",
    )
    p.add_argument(
        "--user-id",
        required=True,
        help=(
            "UUID of the OAuth'd user whose Kite credentials should "
            "be loaded from algo.broker_credentials."
        ),
    )
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument(
        "--source",
        default="manual_backfill",
        help="Value written to the ``source`` column.",
    )
    return p


async def _resolve_kite_async(user_id_str: str) -> Any:
    """Build a KiteClient from the user's stored credentials."""
    from uuid import UUID

    from backend.algo.broker.credentials_repo import (
        BrokerCredentialsRepo,
    )
    from backend.algo.broker.kite_client import KiteClient
    from backend.db.engine import get_session_factory

    user_id = UUID(user_id_str)
    repo = BrokerCredentialsRepo()
    factory = get_session_factory()
    async with factory() as session:
        creds = await repo.load(session, user_id)
    if not creds:
        raise RuntimeError(
            f"No broker credentials stored for user {user_id_str}; "
            f"complete the Kite OAuth handshake first.",
        )
    if creds.get("access_token_expired") or not creds.get(
        "access_token",
    ):
        raise RuntimeError(
            f"Kite access_token for user {user_id_str} is expired "
            f"or missing; re-run the OAuth handshake.",
        )
    return KiteClient(
        api_key=creds["api_key"],
        access_token=creds["access_token"],
        dry_run=False,
    )


async def _resolve_tokens_async(
    tickers: list[str],
) -> dict[str, int]:
    """``ticker → instrument_token`` via ``algo.instruments``."""
    from backend.algo.instruments.repo import InstrumentsRepo
    from backend.db.engine import get_session_factory

    repo = InstrumentsRepo()
    factory = get_session_factory()
    async with factory() as session:
        token_to_ticker = await repo.get_tokens_for_tickers(
            session,
            tickers,
        )
    return {t: tok for tok, t in token_to_ticker.items()}


def _resolve_universe(name: str, anchor: date) -> list[str]:
    """Resolve the requested ticker cohort.

    ``nifty500`` reads the same CSV the slice-1d keeper uses
    (single source of truth — the operator's one-shot backfill
    and the daily keeper cover identical sets). ``india_top200``
    / ``india_full`` read ``stocks.universe_snapshot``.
    """
    if name == "nifty500":
        from backend.algo.jobs.intraday_bars_daily_ingest import (
            _resolve_nifty500_universe,
        )

        return _resolve_nifty500_universe()
    if name == "india_top200":
        from backend.algo.universe.pit_resolver import (
            resolve_pit_universe,
        )

        return resolve_pit_universe(anchor)
    # india_full → every ticker ever included in the latest
    # rebalance row, top-200 flag ignored.
    rows = query_iceberg_table(
        "stocks.universe_snapshot",
        "SELECT DISTINCT ticker FROM universe_snapshot "
        "WHERE rebalance_date = ("
        "  SELECT MAX(rebalance_date) FROM universe_snapshot"
        ") ORDER BY ticker",
        [],
    )
    return [r["ticker"] for r in rows or []]


def _main(argv: list[str] | None = None) -> int:
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    args = _build_arg_parser().parse_args(argv)
    if args.start > args.end:
        _logger.error(
            "--start %s is after --end %s — nothing to backfill",
            args.start,
            args.end,
        )
        return 2

    if args.tickers:
        tickers = sorted(
            {t.strip() for t in args.tickers.split(",") if t.strip()}
        )
    else:
        tickers = _resolve_universe(args.universe, args.end)
    if not tickers:
        _logger.error("No tickers to backfill — abort")
        return 2

    # Single event loop for all async PG work — two separate
    # ``asyncio.run`` calls bind the cached async engine to the
    # first loop, then the second invocation crashes on
    # ``Future attached to a different loop`` (CLAUDE.md §6.7,
    # asyncpg-sync-async-bridge memory).
    async def _bootstrap() -> tuple[Any, dict[str, int]]:
        kite_ = await _resolve_kite_async(args.user_id)
        tokens_ = await _resolve_tokens_async(tickers)
        return kite_, tokens_

    kite, tokens = asyncio.run(_bootstrap())
    stats = backfill_window(
        kite=kite,
        tickers=tickers,
        instrument_tokens=tokens,
        interval_sec=args.interval,
        start=args.start,
        end=args.end,
        source=args.source,
        batch_size=args.batch_size,
    )
    if stats.tickers_failed:
        _logger.warning(
            "Backfill finished with %d failures (sample: %s)",
            stats.tickers_failed,
            stats.failures[:5],
        )
        return 1 if stats.tickers_done == 0 else 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
