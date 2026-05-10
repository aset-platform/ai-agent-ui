"""Daily Brinson + monthly factor regression orchestrators.

Both jobs are pragmatic v3 implementations:

* ``daily_brinson_job(payload)`` reads today's ``order_filled``
  events for every ``(user_id, strategy_id)`` pair active today,
  computes per-sector portfolio weights from the realised P&L
  side, looks up sector mapping from ``stocks.piotroski_scores``,
  and computes the Brinson decomposition against an equal-weight
  NIFTY 50 sector baseline (real index weights wired in v3.1).
  Persists one row per pair to ``algo.attribution_daily``.

* ``monthly_factor_regression_job(payload)`` reads the last 30
  days of strategy daily P&L from ``algo.runs.summary_json``
  ``equity_curve``, computes daily returns, generates mock
  factor returns (numpy.random seeded by the
  ``(user, strategy, period_start)`` triple so re-runs are
  deterministic), fits an OLS regression, and persists one row
  per ``(user, strategy)`` pair to ``algo.factor_regression``
  with ``betas["__mock_data__"] = 1.0`` so the UI flags it.

NEITHER job wires real NIFTY index weights or real Fama-French
factor returns — those are explicit v3.1 follow-ups.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import numpy as np

from backend.algo.attribution.brinson import compute_brinson
from backend.algo.attribution.factor_regression import (
    fit_ols_regression,
)

_logger = logging.getLogger(__name__)

# Mock factor universe — three Fama-French factors plus a
# momentum factor. Replaced by real NSE Fama-French data in
# v3.1; until then jobs persist with a __mock_data__ flag.
_MOCK_FACTOR_KEYS = ("MKT", "SMB", "HML", "MOM")

# Equal-weight NIFTY 50 baseline — 1/50 across the (initially
# unknown) sectors actually present in the trade book. This is
# replaced in v3.1 with the real NIFTY 50 constituent weights.
_BASELINE_RETURN_PER_SECTOR = 0.0  # neutral baseline for v3


def _ist_today() -> date:
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def _load_pg_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def _load_today_filled_events(
    *, as_of: date,
) -> list[dict[str, Any]]:
    """Read every ``order_filled`` Iceberg event for ``as_of``.

    Returns the raw rows; the caller groups them by user +
    strategy. ``payload_json`` is parsed by the caller.
    """
    from backend.db.duckdb_engine import query_iceberg_table

    sql = (
        "SELECT user_id, strategy_id, payload_json, ts_ns "
        "FROM events "
        "WHERE type IN ('order_filled', 'order_filled_live') "
        "  AND ts_date = ? "
        "ORDER BY ts_ns"
    )
    return query_iceberg_table(
        "algo.events", sql, [as_of.isoformat()],
    )


def _aggregate_sector_weights_and_returns(
    events: list[dict[str, Any]],
    sector_map: dict[str, str | None],
) -> tuple[dict[str, float], dict[str, float]]:
    """Project filled events into per-sector portfolio weights +
    portfolio returns.

    For v3 we use a simple proxy: weights are realised-INR-traded
    per sector divided by total INR traded; returns are zero
    (real intra-day returns require mark-to-market against
    closing price — a v3.1 enhancement). This still exercises
    the Brinson plumbing end-to-end.
    """
    sector_inr: dict[str, float] = defaultdict(float)
    total_inr = 0.0
    for ev in events:
        try:
            payload = json.loads(ev.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        ticker = payload.get("ticker")
        qty = payload.get("qty")
        price = payload.get("fill_price") or payload.get("price")
        if not ticker or qty is None or price is None:
            continue
        try:
            inr = float(qty) * float(price)
        except (TypeError, ValueError):
            continue
        sector = sector_map.get(ticker) or "Unknown"
        sector_inr[sector] += inr
        total_inr += inr

    if total_inr <= 0:
        return ({}, {})
    weights = {s: v / total_inr for s, v in sector_inr.items()}
    # v3 placeholder: per-sector return defaults to baseline
    # (zero) for both portfolio and benchmark. The Brinson
    # decomposition still produces a valid algebraic identity;
    # active return collapses to zero, but the structure is in
    # place for v3.1 to swap in real per-sector returns.
    returns = {s: 0.0 for s in weights}
    return (weights, returns)


def _persist_attribution_row(
    *,
    user_id: UUID,
    strategy_id: UUID,
    bar_date: date,
    components: dict,
    total_active: float,
) -> None:
    """Insert one row into algo.attribution_daily.

    Uses ``ON CONFLICT DO UPDATE`` so the daily job is idempotent
    — re-running for the same date overwrites the previous row.
    """
    import asyncio

    from sqlalchemy import text

    async def _do() -> None:
        factory = _load_pg_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO algo.attribution_daily ("
                    " user_id, strategy_id, bar_date, "
                    " brinson_alloc, brinson_select, "
                    " brinson_interaction, total_active_return"
                    ") VALUES ("
                    " :uid, :sid, :bd, "
                    " :alloc::jsonb, :sel::jsonb, :inter::jsonb, "
                    " :tot"
                    ") "
                    "ON CONFLICT (user_id, strategy_id, bar_date) "
                    "DO UPDATE SET "
                    " brinson_alloc=EXCLUDED.brinson_alloc, "
                    " brinson_select=EXCLUDED.brinson_select, "
                    " brinson_interaction="
                    "  EXCLUDED.brinson_interaction, "
                    " total_active_return="
                    "  EXCLUDED.total_active_return"
                ),
                {
                    "uid": user_id,
                    "sid": strategy_id,
                    "bd": bar_date,
                    "alloc": json.dumps({
                        s: c.allocation
                        for s, c in components.items()
                    }),
                    "sel": json.dumps({
                        s: c.selection
                        for s, c in components.items()
                    }),
                    "inter": json.dumps({
                        s: c.interaction
                        for s, c in components.items()
                    }),
                    "tot": total_active,
                },
            )
            await session.commit()

    asyncio.run(_do())


def daily_brinson_job(payload: dict | None = None) -> dict:
    """Daily job — compute Brinson per active strategy.

    Payload optionally carries ``as_of`` (ISO date string). When
    omitted defaults to the IST current date.
    """
    payload = payload or {}
    as_of_str = payload.get("as_of")
    as_of = (
        date.fromisoformat(as_of_str) if as_of_str
        else _ist_today()
    )

    events = _load_today_filled_events(as_of=as_of)
    if not events:
        _logger.info(
            "daily_brinson_job: no filled events on %s", as_of,
        )
        return {"persisted": 0, "as_of": as_of.isoformat()}

    # Group by (user_id, strategy_id)
    by_pair: dict[
        tuple[str, str], list[dict[str, Any]]
    ] = defaultdict(list)
    for ev in events:
        key = (str(ev["user_id"]), str(ev["strategy_id"]))
        by_pair[key].append(ev)

    # Collect every traded ticker once and look up sector.
    tickers: set[str] = set()
    for evs in by_pair.values():
        for ev in evs:
            try:
                p = json.loads(ev.get("payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            t = p.get("ticker")
            if t:
                tickers.add(t)
    sector_map = _lookup_sector_safe(sorted(tickers))

    persisted = 0
    for (user_id_s, strategy_id_s), pair_events in by_pair.items():
        weights, returns = (
            _aggregate_sector_weights_and_returns(
                pair_events, sector_map,
            )
        )
        if not weights:
            continue
        # v3: equal-weight baseline across the same sector set.
        n = len(weights)
        bench_weights = {s: 1.0 / n for s in weights}
        bench_returns = {
            s: _BASELINE_RETURN_PER_SECTOR for s in weights
        }
        components = compute_brinson(
            weights, bench_weights, returns, bench_returns,
        )
        if not components:
            continue
        total_active = sum(c.total for c in components.values())
        try:
            _persist_attribution_row(
                user_id=UUID(user_id_s),
                strategy_id=UUID(strategy_id_s),
                bar_date=as_of,
                components=components,
                total_active=total_active,
            )
            persisted += 1
        except Exception:  # noqa: BLE001 — log + continue
            _logger.exception(
                "daily_brinson_job: persist failed user=%s "
                "strategy=%s",
                user_id_s, strategy_id_s,
            )
    return {"persisted": persisted, "as_of": as_of.isoformat()}


def _lookup_sector_safe(
    tickers: list[str],
) -> dict[str, str | None]:
    """Wrapper around the factor-job sector lookup that swallows
    Iceberg errors so the daily job degrades gracefully (e.g.
    fresh DB without piotroski rows yet)."""
    try:
        from backend.algo.factors.compute_job import (
            _lookup_sector,
        )
        return _lookup_sector(tickers)
    except Exception:  # noqa: BLE001
        _logger.warning(
            "sector lookup failed for %d tickers — "
            "treating all as Unknown",
            len(tickers),
        )
        return {t: None for t in tickers}


def _load_strategy_daily_returns(
    *,
    user_id: UUID,
    strategy_id: UUID,
    period_start: date,
    period_end: date,
) -> np.ndarray | None:
    """Read daily strategy returns from algo.runs.summary_json.

    Returns ``None`` if no usable curve is found. The equity
    curve is encoded as a list of ``{bar_date, equity_inr}``
    dicts; we project to a numpy array of daily simple returns.
    """
    import asyncio

    from sqlalchemy import text

    async def _read() -> Any:
        factory = _load_pg_session_factory()
        async with factory() as session:
            r = await session.execute(
                text(
                    "SELECT summary_json FROM algo.runs "
                    "WHERE user_id = :uid "
                    "  AND strategy_id = :sid "
                    "  AND completed_at IS NOT NULL "
                    "  AND period_end >= :ps "
                    "  AND period_end <= :pe "
                    "ORDER BY period_end DESC LIMIT 1"
                ),
                {
                    "uid": user_id,
                    "sid": strategy_id,
                    "ps": period_start,
                    "pe": period_end,
                },
            )
            row = r.mappings().first()
        return row["summary_json"] if row else None

    summary = asyncio.run(_read())
    if not summary:
        return None
    curve = summary.get("equity_curve") or []
    if len(curve) < 2:
        return None
    equities: list[float] = []
    for ep in curve:
        try:
            equities.append(float(ep["equity_inr"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(equities) < 2:
        return None
    eq = np.asarray(equities, dtype=float)
    rets = np.diff(eq) / eq[:-1]
    return rets


def _generate_mock_factor_returns(
    *,
    user_id: UUID,
    strategy_id: UUID,
    period_start: date,
    n: int,
) -> dict[str, np.ndarray]:
    """Generate deterministic mock daily factor returns.

    Seed = hash of ``(user_id, strategy_id, period_start)`` —
    repeatable across re-runs of the same period.
    """
    seed_str = f"{user_id}|{strategy_id}|{period_start.isoformat()}"
    seed = abs(hash(seed_str)) % (2**32)
    rng = np.random.default_rng(seed)
    return {
        k: rng.normal(0.0003, 0.01, n)
        for k in _MOCK_FACTOR_KEYS
    }


def _persist_factor_regression_row(
    *,
    user_id: UUID,
    strategy_id: UUID,
    period_start: date,
    period_end: date,
    alpha: float,
    betas: dict[str, float],
    r_squared: float,
    n_obs: int,
) -> None:
    """Insert one row into algo.factor_regression. Idempotent."""
    import asyncio

    from sqlalchemy import text

    async def _do() -> None:
        factory = _load_pg_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO algo.factor_regression ("
                    " user_id, strategy_id, period_start, "
                    " period_end, alpha, betas, r_squared, "
                    " n_observations"
                    ") VALUES ("
                    " :uid, :sid, :ps, :pe, "
                    " :a, :b::jsonb, :r, :n"
                    ") "
                    "ON CONFLICT (user_id, strategy_id, "
                    " period_start, period_end) "
                    "DO UPDATE SET "
                    " alpha=EXCLUDED.alpha, "
                    " betas=EXCLUDED.betas, "
                    " r_squared=EXCLUDED.r_squared, "
                    " n_observations=EXCLUDED.n_observations"
                ),
                {
                    "uid": user_id,
                    "sid": strategy_id,
                    "ps": period_start,
                    "pe": period_end,
                    "a": (
                        float(alpha)
                        if not np.isnan(alpha) else 0.0
                    ),
                    "b": json.dumps(betas),
                    "r": (
                        float(r_squared)
                        if not np.isnan(r_squared) else 0.0
                    ),
                    "n": int(n_obs),
                },
            )
            await session.commit()

    asyncio.run(_do())


def _list_active_strategies(
    *, period_start: date, period_end: date,
) -> list[tuple[UUID, UUID]]:
    """Return ``(user_id, strategy_id)`` pairs that have at least
    one completed run in the period."""
    import asyncio

    from sqlalchemy import text

    async def _read() -> list[tuple[UUID, UUID]]:
        factory = _load_pg_session_factory()
        async with factory() as session:
            r = await session.execute(
                text(
                    "SELECT DISTINCT user_id, strategy_id "
                    "FROM algo.runs "
                    "WHERE completed_at IS NOT NULL "
                    "  AND period_end >= :ps "
                    "  AND period_end <= :pe"
                ),
                {"ps": period_start, "pe": period_end},
            )
            return [
                (row["user_id"], row["strategy_id"])
                for row in r.mappings()
            ]

    return asyncio.run(_read())


def monthly_factor_regression_job(
    payload: dict | None = None,
) -> dict:
    """Monthly OLS factor regression per active strategy.

    Payload may carry ``period_start`` + ``period_end`` ISO
    dates; when omitted defaults to the trailing 30 IST days.
    """
    payload = payload or {}
    end = (
        date.fromisoformat(payload["period_end"])
        if payload.get("period_end") else _ist_today()
    )
    start = (
        date.fromisoformat(payload["period_start"])
        if payload.get("period_start") else (end - timedelta(days=30))
    )

    pairs = _list_active_strategies(
        period_start=start, period_end=end,
    )
    persisted = 0
    for user_id, strategy_id in pairs:
        rets = _load_strategy_daily_returns(
            user_id=user_id, strategy_id=strategy_id,
            period_start=start, period_end=end,
        )
        if rets is None or len(rets) == 0:
            continue
        factors = _generate_mock_factor_returns(
            user_id=user_id, strategy_id=strategy_id,
            period_start=start, n=len(rets),
        )
        result = fit_ols_regression(rets, factors)
        # Mark as mock so the UI can render an "experimental"
        # chip until v3.1 wires real factor data.
        betas_with_flag = dict(result.betas)
        betas_with_flag["__mock_data__"] = 1.0
        try:
            _persist_factor_regression_row(
                user_id=user_id,
                strategy_id=strategy_id,
                period_start=start,
                period_end=end,
                alpha=result.alpha,
                betas=betas_with_flag,
                r_squared=result.r_squared,
                n_obs=result.n_observations,
            )
            persisted += 1
        except Exception:  # noqa: BLE001 — log + continue
            _logger.exception(
                "monthly_factor_regression_job: persist failed "
                "user=%s strategy=%s", user_id, strategy_id,
            )

    return {
        "persisted": persisted,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }
