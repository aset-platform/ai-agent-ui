"""Promotion-workflow gates + eligibility checks.

The workflow is::

    draft  --backtest+walkforward--> paper
    paper  --paper run-->            live

Edits to the AST auto-demote a non-draft strategy back to draft
(handled in ``update_strategy``). Once a strategy has ever been
``live``, the bypass card is offered in the Promote dialog so the
operator can re-promote straight to live after a future edit.

This module owns the gate-check SQL and the eligibility response
shape consumed by the frontend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.strategy.mode_repo import (
    ALL_MODES,
    MODE_DRAFT,
    MODE_LIVE,
    MODE_PAPER,
    has_ever_been,
)


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    MODE_DRAFT: {MODE_PAPER},
    MODE_PAPER: {MODE_LIVE},
    MODE_LIVE: set(),
}


@dataclass
class TransitionEligibility:
    """One target mode's gate state."""

    target: str
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    # bypass_available is meaningful only on the path to ``live``
    # — the workflow has no fast-lane to ``paper``.
    bypass_available: bool = False


@dataclass
class EligibilityResponse:
    current_mode: str
    transitions: list[TransitionEligibility]


def _is_legal_step(current: str, target: str) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


@dataclass
class _RunStats:
    """Diagnostic counters surfaced into gate-failure reasons."""
    total: int
    fresh: int  # >= strategies.updated_at
    updated_at_iso: str | None


async def _completed_run_stats(
    session: AsyncSession,
    *,
    strategy_id: UUID,
    mode: str,
) -> _RunStats:
    """For backtest / walkforward — count rows in algo.runs.

    Paper runs are tracked via Iceberg events (algo.events) and
    go through ``_paper_fill_stats`` instead.
    """
    rows = (
        await session.execute(
            text(
                "SELECT "
                "  count(*) FILTER (WHERE r.status='completed' "
                "    AND r.error_text IS NULL) AS total, "
                "  count(*) FILTER (WHERE r.status='completed' "
                "    AND r.error_text IS NULL "
                "    AND r.started_at >= s.updated_at) AS fresh, "
                "  max(s.updated_at) AS updated_at "
                "FROM algo.runs r "
                "JOIN algo.strategies s ON s.id = r.strategy_id "
                "WHERE r.strategy_id = :sid AND r.mode = :mode"
            ),
            {"sid": str(strategy_id), "mode": mode},
        )
    ).mappings().first()
    return _RunStats(
        total=int((rows or {}).get("total") or 0),
        fresh=int((rows or {}).get("fresh") or 0),
        updated_at_iso=(
            rows["updated_at"].isoformat()
            if rows and rows.get("updated_at") is not None
            else None
        ),
    )


async def _paper_fill_stats(
    session: AsyncSession,
    *,
    strategy_id: UUID,
) -> _RunStats:
    """Paper sessions don't create algo.runs rows — they emit
    ``order_filled`` events into Iceberg ``algo.events``. The
    presence of at-least-one such event since the last AST edit
    is what "completed paper run" means for the gate.

    Iceberg scan is sync; wrapped in ``asyncio.to_thread`` so it
    doesn't block the event loop.
    """
    upd_row = (
        await session.execute(
            text(
                "SELECT updated_at FROM algo.strategies "
                "WHERE id = :sid"
            ),
            {"sid": str(strategy_id)},
        )
    ).first()
    if upd_row is None:
        return _RunStats(total=0, fresh=0, updated_at_iso=None)
    updated_at = upd_row[0]
    upd_ns = int(updated_at.timestamp() * 1_000_000_000)

    import asyncio

    def _scan() -> tuple[int, int]:
        from backend.algo.iceberg_init import _get_catalog
        try:
            cat = _get_catalog()
            tbl = cat.load_table("algo.events")
            df = tbl.scan(
                row_filter=(
                    f"strategy_id = '{strategy_id}' AND "
                    f"mode = 'paper' AND type = 'order_filled'"
                ),
            ).to_pandas()
        except Exception:  # noqa: BLE001
            # Iceberg unavailable / table missing — fail open
            # (zero events seen) so the gate behaviour stays
            # explicit (user sees "0 fresh paper fills").
            return 0, 0
        total = len(df)
        if total == 0:
            return 0, 0
        fresh = int((df["ts_ns"] >= upd_ns).sum())
        return total, fresh

    total, fresh = await asyncio.to_thread(_scan)
    return _RunStats(
        total=total,
        fresh=fresh,
        updated_at_iso=updated_at.isoformat(),
    )


async def check_eligibility(
    session: AsyncSession,
    *,
    strategy_id: UUID,
    current_mode: str,
) -> EligibilityResponse:
    """Return per-target-mode eligibility for the Promote dialog.

    Only forward transitions are evaluated — there is no manual
    demote in the UI. ``bypass_available`` is only true on the
    path to ``live`` and only when the strategy has ever held
    ``mode='live'`` (the "earned re-promotion" rule).
    """
    transitions: list[TransitionEligibility] = []

    for target in (MODE_PAPER, MODE_LIVE):
        # Bypass is computed independently of legality — the
        # whole point is to skip a normally-illegal step
        # (e.g. draft → live in one click) when the strategy has
        # earned the right by having been live before.
        bypass_available = False
        if target == MODE_LIVE:
            bypass_available = await has_ever_been(
                session,
                strategy_id=strategy_id,
                mode=MODE_LIVE,
            )

        if not _is_legal_step(current_mode, target):
            transitions.append(
                TransitionEligibility(
                    target=target,
                    allowed=False,
                    reasons=[
                        f"Not a legal one-step transition from "
                        f"{current_mode!r}."
                    ],
                    bypass_available=bypass_available,
                ),
            )
            continue

        reasons: list[str] = []
        if target == MODE_PAPER:
            bt = await _completed_run_stats(
                session, strategy_id=strategy_id, mode="backtest",
            )
            if bt.fresh == 0:
                reasons.append(_gate_reason_bt(bt, "backtest"))
            wf = await _completed_run_stats(
                session, strategy_id=strategy_id, mode="walkforward",
            )
            if wf.fresh == 0:
                reasons.append(_gate_reason_bt(wf, "walk-forward"))
        elif target == MODE_LIVE:
            paper = await _paper_fill_stats(
                session, strategy_id=strategy_id,
            )
            if paper.fresh == 0:
                reasons.append(_gate_reason_paper(paper))

        transitions.append(
            TransitionEligibility(
                target=target,
                allowed=len(reasons) == 0,
                reasons=reasons,
                bypass_available=bypass_available,
            ),
        )

    return EligibilityResponse(
        current_mode=current_mode, transitions=transitions,
    )


def is_known_mode(mode: str) -> bool:
    return mode in ALL_MODES


def can_take_legal_step(current: str, target: str) -> bool:
    return _is_legal_step(current, target)


def _fmt_ist(iso: str | None) -> str:
    if iso is None:
        return "(unknown)"
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return (
        dt.astimezone(ZoneInfo("Asia/Kolkata"))
        .strftime("%Y-%m-%d %H:%M IST")
    )


def _gate_reason_bt(stats: _RunStats, label: str) -> str:
    """Human-readable reason for a missing backtest /
    walk-forward run, with concrete counts."""
    edit = _fmt_ist(stats.updated_at_iso)
    if stats.total == 0:
        return (
            f"No {label} run on record. Run one in the "
            f"Backtest tab after this edit ({edit})."
        )
    return (
        f"Found {stats.total} prior {label} run(s), but none "
        f"started after the latest AST edit ({edit}). Editing "
        f"invalidates older runs — run a fresh {label} and try "
        f"again."
    )


def _gate_reason_paper(stats: _RunStats) -> str:
    """Reason for the paper→live gate, with diagnostic counts.

    Paper "completion" is defined as ``order_filled`` events
    with ``mode='paper'`` in ``algo.events`` — paper runtime
    doesn't create algo.runs rows.
    """
    edit = _fmt_ist(stats.updated_at_iso)
    if stats.total == 0:
        return (
            f"No paper fills on record yet. Start a paper run "
            f"in the Paper tab, let it execute at least one "
            f"order, and try again (latest edit: {edit})."
        )
    return (
        f"Found {stats.total} paper fill(s), but 0 since the "
        f"latest AST edit ({edit}). Editing invalidates older "
        f"paper sessions — start a fresh paper run and let it "
        f"execute at least one order."
    )
