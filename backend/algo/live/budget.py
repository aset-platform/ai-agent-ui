"""High-level budget API used by the gate + runtime.

Four 5s-cached helpers (load_user_budget,
sum_open_position_cost, sum_active_reservations,
fetch_kite_available_cash) + two-arg reservation
lifecycle API (reserve, transition).

Caches invalidated on every reservation insert via a
shared _invalidate_cache helper.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.live.budget_repo import BudgetRepo
from backend.algo.live.budget_types import (
    BudgetReservation,
    ReservationState,
    UserBudget,
)

_logger = logging.getLogger(__name__)

_CACHE_TTL_S = 5


def _session_factory():
    """Return a loop-independent session factory.

    Uses disposable_pg_session (NullPool) so budget functions work
    from both the uvicorn event loop (live runtime, FastAPI routes)
    AND from the scheduler's asyncio.run() loop.  get_session_factory()
    is bound to the uvicorn loop and raises "Future attached to a
    different loop" when called from asyncio.run().
    """
    from backend.db.engine import disposable_pg_session

    return disposable_pg_session


def _cache_keys(user_id: UUID) -> tuple[str, str, str]:
    return (
        f"cache:budget:user:{user_id}:open_pos_cost",
        f"cache:budget:user:{user_id}:active_reserved",
        f"cache:budget:user:{user_id}:kite_available",
    )


def _invalidate_cache(user_id: UUID) -> None:
    try:
        from backend.cache import get_cache

        c = get_cache()
        if c:
            c.invalidate_exact(*_cache_keys(user_id))
            # Per-strategy active-reservation keys live under the
            # same per-user prefix but carry a strategy_id we don't
            # have here; glob-sweep them so a fresh reservation
            # never reads a stale strategy total.
            c.invalidate(f"cache:budget:user:{user_id}:strat_active:*")
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "budget cache invalidate failed: %s",
            exc,
            exc_info=True,
        )


async def _build_kite_for_user(user_id: UUID):
    """Construct a real-mode KiteClient for the user, or
    raise RuntimeError if creds are missing or expired.

    Mirrors backend/algo/routes/live.py::
    _build_kite_client_for_user but raises plain
    RuntimeError so the budget fail-open catch handles it.
    """
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.broker.credentials_repo import (
        BrokerCredentialsRepo,
    )

    creds_repo = BrokerCredentialsRepo()
    factory = _session_factory()
    async with factory() as session:
        creds = await creds_repo.load(session, user_id)
    if not creds:
        raise RuntimeError("Kite not connected")
    if creds.get("access_token_expired"):
        raise RuntimeError("Kite token expired")
    api_key = creds.get("api_key")
    access_token = creds.get("access_token")
    if not api_key or not access_token:
        raise RuntimeError("Kite credentials incomplete")
    return KiteClient(
        api_key=api_key,
        access_token=access_token,
        dry_run=False,
    )


async def _kite_margins_for_user(
    user_id: UUID,
) -> dict[str, Any]:
    """Fetch Kite margins for the user.

    Builds a KiteClient, calls ``kc._kc.margins('equity')``
    in a thread. Raises if creds are missing or Kite errors.
    """
    kc = await _build_kite_for_user(user_id)
    return await asyncio.to_thread(kc._kc.margins, "equity")


async def _algo_filled_events_for_user(
    user_id: UUID,
) -> list[dict[str, Any]]:
    """Return the user's open-position cost-basis events.

    TODO(Task 5): wire to algo.events FILL rows (subtract matching
    SELLs). For now returns []; tests mock this function so
    the helper still works with full coverage.
    """
    return []


async def load_user_budget(user_id: UUID) -> UserBudget:
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        return await repo.get_user_budget(
            session,
            user_id=user_id,
        )


async def sum_open_position_cost(user_id: UUID) -> Decimal:
    """Sum cost basis across open positions.

    Cache: 5s per user; invalidated on reservation inserts.
    """
    from backend.cache import get_cache

    c = get_cache()
    key = _cache_keys(user_id)[0]
    if c:
        cached = c.get(key)
        if cached is not None:
            return Decimal(cached)

    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        total = await repo.sum_open_position_cost(
            session,
            user_id=user_id,
        )
    if c:
        c.set(key, str(total), ttl=_CACHE_TTL_S)
    return total


async def sum_active_reservations(
    user_id: UUID,
) -> Decimal:
    from backend.cache import get_cache

    c = get_cache()
    key = _cache_keys(user_id)[1]
    if c:
        cached = c.get(key)
        if cached is not None:
            return Decimal(cached)

    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        total = await repo.sum_active_reservations(
            session,
            user_id=user_id,
        )
    if c:
        c.set(key, str(total), ttl=_CACHE_TTL_S)
    return total


async def sum_active_reservations_for_strategy(
    user_id: UUID,
    strategy_id: UUID,
) -> Decimal:
    """Active BUY reservation total for one strategy.

    Feeds the runtime's deployed-capital calc (deployed = filled
    positions + in-flight reservations) so two BUYs in one tick
    can't both size against the same remaining max_inr (Critical
    C4). Cache: 5s per (user, strategy); invalidated on every
    reservation insert via ``_invalidate_cache`` (the key shares
    the ``cache:budget:user:<uid>:`` prefix swept there).
    """
    from backend.cache import get_cache

    c = get_cache()
    key = (
        f"cache:budget:user:{user_id}"
        f":strat_active:{strategy_id}"
    )
    if c:
        cached = c.get(key)
        if cached is not None:
            return Decimal(cached)

    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        total = await repo.sum_active_reservations_for_strategy(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
        )
    if c:
        c.set(key, str(total), ttl=_CACHE_TTL_S)
    return total


async def fetch_kite_available_cash(
    user_id: UUID,
) -> Decimal:
    """kite.margins.equity.available.cash; Decimal('0') on
    Kite error (fail-closed — blocks new live orders until the
    broker connection is restored).

    Dry-run bypass lives at the call site in safety.py and is
    unaffected: dry_run paths return Decimal('Infinity') there
    so this function is never called for dry-run.
    """
    from backend.cache import get_cache

    c = get_cache()
    key = _cache_keys(user_id)[2]
    if c:
        cached = c.get(key)
        if cached is not None:
            return Decimal(cached)

    try:
        margins = await _kite_margins_for_user(user_id)
        # kc.margins("equity") returns the equity segment directly —
        # no nested "equity" key. Use live_balance (consistent with
        # the algo header CASH stat) — it includes intraday payin
        # from sells and matches what Kite allows for new CNC orders.
        # available.cash deducts CNC blocks separately which would
        # double-count against our own open_pos_cost tracking.
        #
        # Explicit None check so a legitimate zero live_balance is
        # honored and not masked by the cash fallback (the old `or`
        # treated 0 as falsy and silently fell through to cash).
        avail = margins.get("available", {})
        lb = avail.get("live_balance")
        raw = lb if lb is not None else avail.get("cash", 0)
        out = Decimal(str(raw))
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "kite margins fetch failed for user=%s: %s — "
            "broker-cash cap set to 0 (fail-closed)",
            user_id,
            exc,
            exc_info=True,
        )
        return Decimal("0")

    if c:
        c.set(key, str(out), ttl=_CACHE_TTL_S)
    return out


async def reserve(
    *,
    user_id: UUID,
    strategy_id: UUID,
    ticker: str,
    side: str,
    qty: int,
    reserved_inr: Decimal,
    metadata: dict[str, Any] | None = None,
) -> UUID:
    """Acquire a PENDING reservation. Returns the new
    reservation_id; subsequent transition() calls thread
    through it."""
    reservation_id = uuid4()
    row = BudgetReservation(
        reservation_id=reservation_id,
        user_id=user_id,
        strategy_id=strategy_id,
        state=ReservationState.PENDING,
        ticker=ticker,
        side=side,
        qty=qty,
        reserved_inr=reserved_inr,
        transitioned_at=datetime.now(timezone.utc),
        metadata=metadata or {},
    )
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        await repo.insert_reservation_event(session, row)
        await session.commit()
    _invalidate_cache(user_id)
    return reservation_id


async def reserve_if_headroom(
    *,
    user_id: UUID,
    strategy_id: UUID,
    ticker: str,
    side: str,
    qty: int,
    reserved_inr: Decimal,
    allocated_inr: Decimal,
    metadata: dict[str, Any] | None = None,
) -> UUID | None:
    """Atomic, uncached, headroom-aware reservation wrapper.

    Opens ONE NullPool session, delegates to
    ``BudgetRepo.reserve_if_headroom`` (``SELECT ... FOR UPDATE``
    on ``algo.user_budget`` + uncached headroom recompute +
    conditional PENDING insert), then commits — holding the per-
    user lock for the whole txn so concurrent reservers serialize.

    Returns the new ``reservation_id`` on success, else ``None``
    when ``headroom < reserved_inr``. This is the atomic primitive
    that supersedes the non-atomic check-then-reserve flow; the
    runtime/safety rewire to call it lands in Task 2.2.
    """
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        reservation_id = await repo.reserve_if_headroom(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            ticker=ticker,
            side=side,
            qty=qty,
            reserved_inr=reserved_inr,
            allocated_inr=allocated_inr,
            metadata=metadata,
        )
        await session.commit()
    if reservation_id is not None:
        _invalidate_cache(user_id)
    return reservation_id


async def transition(
    *,
    reservation_id: UUID,
    new_state: ReservationState,
    kite_order_id: str | None = None,
    filled_qty: int | None = None,
    filled_inr: Decimal | None = None,
    error_text: str | None = None,
) -> None:
    """Append a new state-event row, inheriting unchanged
    fields from the previous row of this reservation_id.

    Concurrent transitions from the same prev row are tolerated
    by the append-only event log -- the latest insert wins.
    """
    repo = BudgetRepo()
    factory = _session_factory()
    async with factory() as session:
        prev = await repo.get_current_state(
            session,
            reservation_id=reservation_id,
        )
        if prev is None:
            _logger.error(
                "transition: no prior reservation %s",
                reservation_id,
            )
            return
        row = BudgetReservation(
            reservation_id=reservation_id,
            user_id=prev.user_id,
            strategy_id=prev.strategy_id,
            state=new_state,
            ticker=prev.ticker,
            side=prev.side,
            qty=prev.qty,
            reserved_inr=prev.reserved_inr,
            filled_qty=(
                filled_qty if filled_qty is not None else prev.filled_qty
            ),
            filled_inr=(
                filled_inr if filled_inr is not None else prev.filled_inr
            ),
            kite_order_id=(kite_order_id or prev.kite_order_id),
            transitioned_at=datetime.now(timezone.utc),
            metadata=prev.metadata,
            error_text=error_text,
        )
        await repo.insert_reservation_event(session, row)
        await session.commit()
        _invalidate_cache(prev.user_id)
