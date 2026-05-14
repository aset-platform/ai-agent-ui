"""Unit tests for the promotion-workflow helpers.

Covers ``hash_ast`` stability, ``check_eligibility`` gate decisions
under various combinations of (current_mode, prior history,
completed runs). The end-to-end PATCH route is exercised in
``test_strategies_routes.py``; this file targets the pure
calculation layer.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.algo.strategy.mode_repo import (
    MODE_DRAFT,
    MODE_LIVE,
    MODE_PAPER,
    hash_ast,
)
from backend.algo.strategy.promotion import (
    can_take_legal_step,
    check_eligibility,
    is_known_mode,
)


def test_hash_ast_is_stable_across_key_order():
    ast_a = {"name": "x", "id": "1", "root": {"type": "hold"}}
    ast_b = {"id": "1", "root": {"type": "hold"}, "name": "x"}
    assert hash_ast(ast_a) == hash_ast(ast_b)


def test_hash_ast_changes_with_value():
    ast = {"name": "x", "id": "1"}
    h0 = hash_ast(ast)
    h1 = hash_ast({**ast, "name": "y"})
    assert h0 != h1


def test_legal_step_matrix():
    # Only forward by one step; no demote, no skipping levels.
    assert can_take_legal_step(MODE_DRAFT, MODE_PAPER)
    assert can_take_legal_step(MODE_PAPER, MODE_LIVE)
    assert not can_take_legal_step(MODE_DRAFT, MODE_LIVE)
    assert not can_take_legal_step(MODE_LIVE, MODE_PAPER)
    assert not can_take_legal_step(MODE_PAPER, MODE_DRAFT)


def test_is_known_mode():
    assert is_known_mode("draft")
    assert is_known_mode("paper")
    assert is_known_mode("live")
    assert not is_known_mode("running")
    assert not is_known_mode("")


class _StubSession:
    """Minimal AsyncSession stub for the promotion-gate queries.

    Reflects the two query shapes ``check_eligibility`` issues:
      1. ``_completed_run_stats`` — algo.runs join, returns a
         mappings row with ``total / fresh / updated_at``.
      2. ``has_ever_been`` — algo.strategy_mode_transitions,
         returns a single-int tuple via ``.first()``.

    Paper-mode is exercised via the ``_paper_fill_stats`` helper
    which scans Iceberg directly; tests that hit the paper gate
    monkey-patch that helper instead of going through the stub.
    """

    def __init__(
        self,
        *,
        has_backtest_fresh: bool = False,
        has_walkforward_fresh: bool = False,
        has_ever_been_live: bool = False,
    ):
        self.has_backtest_fresh = has_backtest_fresh
        self.has_walkforward_fresh = has_walkforward_fresh
        self.has_ever_been_live = has_ever_been_live

    async def execute(self, q, params=None):
        sql = str(q)

        class _MappingsResult:
            def __init__(self, row):
                self._row = row

            def first(self):
                return self._row

        class _Result:
            def __init__(self, row, mappings_row=None):
                self._row = row
                self._mappings = mappings_row

            def first(self):
                return self._row

            def mappings(self):
                return _MappingsResult(self._mappings)

        if "FROM algo.runs r" in sql:
            mode = (params or {}).get("mode")
            fresh_n = (
                1 if {
                    "backtest": self.has_backtest_fresh,
                    "walkforward": self.has_walkforward_fresh,
                }.get(mode, False) else 0
            )
            row = {"total": fresh_n, "fresh": fresh_n,
                   "updated_at": None}
            return _Result(row=(fresh_n,), mappings_row=row)

        if "FROM algo.strategy_mode_transitions" in sql:
            return _Result(
                row=(1,) if self.has_ever_been_live else None,
            )

        # SELECT updated_at FROM algo.strategies — only fired by
        # _paper_fill_stats. Tests that exercise that path
        # monkey-patch the helper; this fallback returns None so
        # the path stays inert when not patched.
        return _Result(row=None)


@pytest.mark.asyncio
async def test_eligibility_draft_blocked_when_no_runs():
    sess = _StubSession()
    elig = await check_eligibility(
        sess, strategy_id=uuid4(), current_mode=MODE_DRAFT,
    )
    paper = next(t for t in elig.transitions if t.target == "paper")
    live = next(t for t in elig.transitions if t.target == "live")
    assert not paper.allowed
    assert any("backtest" in r for r in paper.reasons)
    assert any("walk-forward" in r for r in paper.reasons)
    # draft → live is not a legal one-step transition; gate path
    # blocked but bypass not offered because never been live.
    assert not live.allowed
    assert not live.bypass_available


@pytest.mark.asyncio
async def test_eligibility_draft_to_paper_passes_with_fresh_runs():
    sess = _StubSession(
        has_backtest_fresh=True, has_walkforward_fresh=True,
    )
    elig = await check_eligibility(
        sess, strategy_id=uuid4(), current_mode=MODE_DRAFT,
    )
    paper = next(t for t in elig.transitions if t.target == "paper")
    assert paper.allowed
    assert paper.reasons == []


@pytest.mark.asyncio
async def test_eligibility_paper_to_live_requires_paper_run(
    monkeypatch: pytest.MonkeyPatch,
):
    """Paper→live gate scans algo.events (not algo.runs) for
    ``order_filled mode='paper'`` events. Stub returns 0 fills."""
    from backend.algo.strategy import promotion as _prom

    async def _no_fills(*_, **__):
        return _prom._RunStats(
            total=0, fresh=0, updated_at_iso=None,
        )

    monkeypatch.setattr(_prom, "_paper_fill_stats", _no_fills)
    sess = _StubSession()
    elig = await check_eligibility(
        sess, strategy_id=uuid4(), current_mode=MODE_PAPER,
    )
    live = next(t for t in elig.transitions if t.target == "live")
    assert not live.allowed
    assert any("paper" in r.lower() for r in live.reasons)


@pytest.mark.asyncio
async def test_eligibility_paper_to_live_passes_with_fresh_fills(
    monkeypatch: pytest.MonkeyPatch,
):
    """Paper→live gate clears when at least one ``order_filled``
    event with ``mode='paper'`` exists after the last AST edit."""
    from backend.algo.strategy import promotion as _prom

    async def _has_fresh_fills(*_, **__):
        return _prom._RunStats(
            total=99, fresh=72,
            updated_at_iso="2026-05-14T09:25:48+00:00",
        )

    monkeypatch.setattr(_prom, "_paper_fill_stats", _has_fresh_fills)
    sess = _StubSession()
    elig = await check_eligibility(
        sess, strategy_id=uuid4(), current_mode=MODE_PAPER,
    )
    live = next(t for t in elig.transitions if t.target == "live")
    assert live.allowed
    assert live.reasons == []


@pytest.mark.asyncio
async def test_gate_reason_paper_carries_concrete_counts(
    monkeypatch: pytest.MonkeyPatch,
):
    """Reason string surfaces the actual fill counts + the edit
    timestamp so users see exactly what's missing."""
    from backend.algo.strategy import promotion as _prom

    async def _stale_fills(*_, **__):
        return _prom._RunStats(
            total=99, fresh=0,
            updated_at_iso="2026-05-14T09:25:48+00:00",
        )

    monkeypatch.setattr(_prom, "_paper_fill_stats", _stale_fills)
    sess = _StubSession()
    elig = await check_eligibility(
        sess, strategy_id=uuid4(), current_mode=MODE_PAPER,
    )
    live = next(t for t in elig.transitions if t.target == "live")
    reason = " ".join(live.reasons).lower()
    assert "99" in reason
    assert "0 since" in reason or "since the latest" in reason


@pytest.mark.asyncio
async def test_eligibility_bypass_only_for_previously_live():
    """Bypass card appears on the live target only when the
    strategy has ever held mode='live' (audit history)."""
    fresh = _StubSession(has_ever_been_live=False)
    elig = await check_eligibility(
        fresh, strategy_id=uuid4(), current_mode=MODE_DRAFT,
    )
    live = next(t for t in elig.transitions if t.target == "live")
    assert not live.bypass_available

    veteran = _StubSession(has_ever_been_live=True)
    elig2 = await check_eligibility(
        veteran, strategy_id=uuid4(), current_mode=MODE_DRAFT,
    )
    live2 = next(t for t in elig2.transitions if t.target == "live")
    assert live2.bypass_available
