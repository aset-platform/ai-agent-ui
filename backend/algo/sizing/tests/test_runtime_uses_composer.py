"""Verify each runtime imports the composer."""
from __future__ import annotations


def test_backtest_runner_imports_composer() -> None:
    from backend.algo.backtest import runner
    assert hasattr(runner, "compose_qty")


def test_paper_runtime_imports_composer() -> None:
    from backend.algo.paper import runtime
    assert hasattr(runtime, "compose_qty")


def test_live_runtime_imports_composer() -> None:
    from backend.algo.live import runtime
    assert hasattr(runtime, "compose_qty")
