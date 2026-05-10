"""3-runtime cache integration test (lightweight binding check)."""
from __future__ import annotations


def test_backtest_runner_imports_factor_cache() -> None:
    from backend.algo.backtest import runner as runner_mod
    assert hasattr(runner_mod, "get_factors_window")


def test_paper_runtime_imports_factor_cache() -> None:
    from backend.algo.paper import runtime as paper_mod
    assert hasattr(paper_mod, "get_factors_window")


def test_live_runtime_imports_factor_cache() -> None:
    from backend.algo.live import runtime as live_mod
    assert hasattr(live_mod, "get_factors_window")
