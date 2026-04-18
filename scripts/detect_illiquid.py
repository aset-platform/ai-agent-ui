"""Detect illiquid tickers by 30-day zero-volume ratio.

A ticker is flagged ``is_tradeable=false`` when more
than ``ZERO_VOL_THRESHOLD`` (default 50%) of its last
30 candles had ``volume=0``.

Run from the project root:

    docker compose exec backend python \\
        scripts/detect_illiquid.py [--dry-run] [--threshold 0.5]

The script is idempotent — flags existing illiquid
tickers as tradeable again if they recover.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta

from sqlalchemy import text

# When executed directly, make /app the cwd-relative root
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

from backend.db.duckdb_engine import (  # noqa: E402
    invalidate_metadata,
    query_iceberg_df,
)
from backend.db.engine import get_session_factory  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("detect_illiquid")

LOOKBACK_DAYS = 30
MIN_CANDLES = 10
ZERO_VOL_THRESHOLD = 0.5


async def _analyzable_tickers() -> set[str]:
    """Return tickers where ticker_type IN (stock, etf).

    Indices and commodities synthetically have
    zero volume — we must not flag them.
    """
    sf = get_session_factory()
    async with sf() as s:
        res = await s.execute(
            text(
                "SELECT ticker FROM stock_registry "
                "WHERE ticker_type IN ('stock','etf')"
            )
        )
        return {r[0] for r in res}


def _scan_illiquid(
    threshold: float,
    lookback_days: int,
    analyzable: set[str],
) -> tuple[set[str], dict[str, float]]:
    """Return (illiquid tickers, per-ticker zero-vol ratio)."""
    invalidate_metadata()
    since = date.today() - timedelta(days=lookback_days)
    df = query_iceberg_df(
        "stocks.ohlcv",
        "SELECT ticker, "
        "  COUNT(*) AS total, "
        "  SUM(CASE WHEN volume = 0 THEN 1 ELSE 0 END) "
        "    AS zero_vol "
        "FROM ohlcv "
        f"WHERE date >= DATE '{since}' "
        "GROUP BY ticker",
    )
    if df.empty:
        return set(), {}

    illiquid: set[str] = set()
    ratios: dict[str, float] = {}
    for _, row in df.iterrows():
        tk = row["ticker"]
        if tk not in analyzable:
            continue
        total = int(row["total"])
        zero = int(row["zero_vol"])
        if total < MIN_CANDLES:
            continue
        ratio = zero / total
        ratios[tk] = ratio
        if ratio > threshold:
            illiquid.add(tk)
    return illiquid, ratios


async def _apply(
    illiquid: set[str],
    dry_run: bool,
) -> tuple[int, int]:
    """UPDATE stock_registry.is_tradeable.

    Returns (flagged_count, unflagged_count).
    """
    sf = get_session_factory()
    async with sf() as s:
        curr = await s.execute(
            text(
                "SELECT ticker FROM stock_registry "
                "WHERE is_tradeable = false"
            )
        )
        already = {r[0] for r in curr}

        to_flag = illiquid - already
        to_unflag = already - illiquid

        log.info(
            "Detected %d illiquid (>%.0f%% zero-vol)",
            len(illiquid),
            ZERO_VOL_THRESHOLD * 100,
        )
        if to_flag:
            log.info("Flagging as illiquid: %s", sorted(to_flag))
        if to_unflag:
            log.info(
                "Restoring to tradeable: %s",
                sorted(to_unflag),
            )
        if not to_flag and not to_unflag:
            log.info("No changes required")
            return 0, 0

        if dry_run:
            log.info("[dry-run] No UPDATE issued")
            return len(to_flag), len(to_unflag)

        if to_flag:
            await s.execute(
                text(
                    "UPDATE stock_registry "
                    "SET is_tradeable = false, "
                    "    updated_at = NOW() "
                    "WHERE ticker = ANY(:tks)"
                ),
                {"tks": list(to_flag)},
            )
        if to_unflag:
            await s.execute(
                text(
                    "UPDATE stock_registry "
                    "SET is_tradeable = true, "
                    "    updated_at = NOW() "
                    "WHERE ticker = ANY(:tks)"
                ),
                {"tks": list(to_unflag)},
            )
        await s.commit()
        return len(to_flag), len(to_unflag)


async def _run(
    threshold: float,
    lookback_days: int,
    dry_run: bool,
) -> int:
    analyzable = await _analyzable_tickers()
    log.info("Scanning %d analyzable tickers", len(analyzable))
    illiquid, ratios = _scan_illiquid(
        threshold,
        lookback_days,
        analyzable,
    )
    if illiquid:
        log.info("Zero-volume ratios (top 10):")
        top = sorted(
            ratios.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )[:10]
        for tk, r in top:
            flag = " *" if tk in illiquid else ""
            log.info("  %-18s %5.1f%%%s", tk, r * 100, flag)

    flagged, unflagged = await _apply(
        illiquid, dry_run,
    )
    log.info(
        "Done. flagged=%d unflagged=%d",
        flagged,
        unflagged,
    )
    return 0


def _main() -> int:
    p = argparse.ArgumentParser(
        description="Flag illiquid tickers "
        "(>50% zero-volume candles in last 30d)",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=ZERO_VOL_THRESHOLD,
        help="Zero-volume ratio threshold (default 0.5)",
    )
    p.add_argument(
        "--lookback-days",
        type=int,
        default=LOOKBACK_DAYS,
        help="Days to scan (default 30)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report but do not write to PG",
    )
    args = p.parse_args()
    return asyncio.run(
        _run(
            args.threshold,
            args.lookback_days,
            args.dry_run,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(_main())
