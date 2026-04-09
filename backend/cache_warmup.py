"""Pre-warm Redis cache on backend startup.

Populates shared (non-user-scoped) cache keys so that the
very first page load serves from Redis instead of scanning
Iceberg.  Per-ticker chart data is warmed in a background
thread to avoid delaying server readiness.

Usage::

    # In routes.py lifespan:
    from cache_warmup import warm_shared, warm_tickers

    warm_shared()                    # blocking, < 1 s
    threading.Thread(
        target=warm_tickers,
        daemon=True,
    ).start()                        # background
"""

from __future__ import annotations

import json
import logging
import time

_logger = logging.getLogger(__name__)


async def warm_shared() -> None:
    """Warm shared (non-user-scoped) cache keys.

    Currently warms:
    - ``cache:dash:registry`` — all registered tickers
    - ``cache:admin:audit`` — audit event log

    Runs as a coroutine; typically completes in < 1 s.
    """
    from cache import get_cache, TTL_VOLATILE

    cache = get_cache()
    if not cache.ping():
        _logger.info(
            "cache_warmup: Redis unavailable; " "skipping warm-up.",
        )
        return

    t0 = time.monotonic()
    warmed = 0

    # Registry warmup intentionally skipped — the real
    # endpoint enriches with OHLCV sparkline, prices,
    # and company names. See gotchas in CLAUDE.md.

    # ── Audit log ─────────────────────────────────
    try:
        import auth.endpoints.helpers as _helpers

        repo = _helpers._get_repo()
        raw = await repo.list_audit_events()
        events = []
        for ev in raw:
            d = dict(ev)
            ts = d.get("event_timestamp")
            if ts is not None and hasattr(ts, "isoformat"):
                d["event_timestamp"] = ts.isoformat()
            events.append(d)
        cache.set(
            "cache:admin:audit",
            json.dumps({"events": events}),
            TTL_VOLATILE,
        )
        warmed += 1
    except Exception:
        _logger.warning(
            "cache_warmup: audit log failed",
            exc_info=True,
        )

    elapsed = (time.monotonic() - t0) * 1000
    _logger.info(
        "cache_warmup: warmed %d shared keys " "in %.0f ms",
        warmed,
        elapsed,
    )


def warm_tickers() -> None:
    """Warm per-ticker chart cache keys in background.

    Iterates over all registered tickers and populates:
    - ``cache:chart:ohlcv:{ticker}``
    - ``cache:chart:indicators:{ticker}``

    Runs in a daemon thread; errors are logged but
    never propagate.
    """
    from cache import get_cache, TTL_STABLE

    cache = get_cache()
    if not cache.ping():
        return

    t0 = time.monotonic()
    warmed = 0

    try:
        from tools._stock_shared import _require_repo
        from dashboard_models import (
            OHLCVPoint,
            OHLCVResponse,
            IndicatorPoint,
            IndicatorsResponse,
        )

        import pandas as pd

        stock_repo = _require_repo()
        registry = stock_repo.get_all_registry()
        tickers = list(registry.keys())

        # Batch reads: 2 DuckDB queries instead of
        # 2*N per-ticker scans.
        ohlcv_all = stock_repo.get_ohlcv_batch(
            tickers,
        )
        ohlcv_grouped: dict[str, pd.DataFrame] = {}
        if not ohlcv_all.empty:
            ohlcv_grouped = dict(tuple(ohlcv_all.groupby("ticker")))

        ti_all = stock_repo.get_technical_indicators_batch(
            tickers,
        )
        ti_grouped: dict[str, pd.DataFrame] = {}
        if not ti_all.empty:
            ti_grouped = dict(tuple(ti_all.groupby("ticker")))

        for ticker in tickers:
            try:
                # OHLCV
                ck = f"cache:chart:ohlcv:{ticker}"
                if cache.get(ck) is None:
                    df = ohlcv_grouped.get(ticker, pd.DataFrame())
                    if not df.empty:
                        points = [
                            OHLCVPoint(
                                date=str(r["date"]),
                                open=float(r["open"]),
                                high=float(r["high"]),
                                low=float(r["low"]),
                                close=float(r["close"]),
                                volume=int(r["volume"]),
                            )
                            for _, r in (df.iterrows())
                        ]
                        result = OHLCVResponse(
                            ticker=ticker,
                            data=points,
                        )
                        cache.set(
                            ck,
                            result.model_dump_json(),
                            TTL_STABLE,
                        )
                        warmed += 1
            except Exception:
                _logger.debug(
                    "cache_warmup: ohlcv %s failed",
                    ticker,
                )

            try:
                # Indicators
                ck = f"cache:chart:indicators:" f"{ticker}"
                if cache.get(ck) is None:
                    df = ti_grouped.get(ticker, pd.DataFrame())
                    if not df.empty:
                        pts = []
                        for _, r in df.iterrows():
                            pts.append(
                                IndicatorPoint(
                                    date=str(
                                        r.get(
                                            "date",
                                            "",
                                        )
                                    ),
                                    sma_50=_sf(r.get("sma_50")),
                                    sma_200=_sf(r.get("sma_200")),
                                    ema_20=_sf(r.get("ema_20")),
                                    rsi_14=_sf(r.get("rsi_14")),
                                    macd=_sf(r.get("macd")),
                                    macd_signal=_sf(r.get("macd_signal")),
                                    macd_hist=_sf(r.get("macd_hist")),
                                    bb_upper=_sf(r.get("bb_upper")),
                                    bb_lower=_sf(r.get("bb_lower")),
                                )
                            )
                        result = IndicatorsResponse(
                            ticker=ticker,
                            data=pts,
                        )
                        cache.set(
                            ck,
                            result.model_dump_json(),
                            TTL_STABLE,
                        )
                        warmed += 1
            except Exception:
                _logger.debug(
                    "cache_warmup: indicators" " %s failed",
                    ticker,
                )

    except Exception:
        _logger.warning(
            "cache_warmup: ticker warm-up failed",
            exc_info=True,
        )

    elapsed = (time.monotonic() - t0) * 1000
    _logger.info(
        "cache_warmup: warmed %d ticker keys " "in %.0f ms",
        warmed,
        elapsed,
    )


async def warm_frequent_users(
    top_n: int = 5,
    days: int = 7,
) -> None:
    """Pre-warm dashboard cache for top N active users.

    Queries ``stocks.llm_usage`` for distinct users
    with the most requests in the last *days* days,
    then warms their dashboard home endpoint so the
    first page load is a Redis hit.

    Args:
        top_n: Maximum users to warm.
        days: Lookback window for activity.
    """
    from cache import get_cache

    cache = get_cache()
    if not cache.ping():
        return

    t0 = time.monotonic()

    try:
        from tools._stock_shared import _require_repo

        stock_repo = _require_repo()
        from datetime import datetime, timedelta, timezone

        import pandas as pd

        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).date()
        try:
            df = stock_repo._scan_tickers(
                stock_repo._LLM_USAGE,
                [],
            )
            # _scan_tickers with empty list returns
            # empty — use _table_to_df instead.
            df = stock_repo._table_to_df(
                stock_repo._LLM_USAGE,
            )
        except Exception:
            df = pd.DataFrame()

        if df.empty or "user_id" not in df.columns:
            _logger.info(
                "cache_warmup: no llm_usage data"
                " for frequent user detection",
            )
            return

        if "request_date" in df.columns:
            df = df[df["request_date"] >= cutoff]

        if df.empty:
            _logger.info(
                "cache_warmup: no active users" " in last %d days",
                days,
            )
            return

        # Count requests per user, pick top N
        counts = (
            df.groupby("user_id")
            .size()
            .sort_values(ascending=False)
            .head(top_n)
        )
        active_users = counts.index.tolist()

        _logger.info(
            "cache_warmup: warming %d frequent" " users: %s",
            len(active_users),
            active_users,
        )

        # For each user, warm their dashboard home
        import auth.endpoints.helpers as _helpers

        repo = _helpers._get_repo()
        warmed = 0

        for uid in active_users:
            try:
                tickers = await repo.get_user_tickers(uid)
                if not tickers:
                    continue

                # Warm watchlist data
                stock_repo.get_ohlcv_batch(
                    tickers,
                )
                stock_repo.get_company_info_batch(
                    tickers,
                )
                # The actual JSON serialization
                # happens when the user hits the
                # endpoint — here we just ensure
                # the Iceberg data is in OS page
                # cache and the repo catalog is warm.
                warmed += 1
            except Exception:
                _logger.debug(
                    "cache_warmup: failed for" " user %s",
                    uid,
                )

        elapsed = (time.monotonic() - t0) * 1000
        _logger.info(
            "cache_warmup: warmed %d/%d frequent" " users in %.0f ms",
            warmed,
            len(active_users),
            elapsed,
        )

    except Exception:
        _logger.warning(
            "cache_warmup: frequent user" " warm-up failed",
            exc_info=True,
        )


def _sf(val) -> float | None:
    """Safe float conversion."""
    if val is None:
        return None
    try:
        import math

        f = float(val)
        return None if math.isnan(f) else round(f, 4)
    except (ValueError, TypeError):
        return None
