"""Wiring proof — PaperRuntime must populate
``nifty_distance_from_sma200_pct`` in the assembled
``EvalContext.features`` (2026-08-10 regime feature, Task 1).

Mirrors ``test_paper_runtime.py``'s ``_bare_runtime()`` /
``_strategy_payload()`` construction. We spy on
``assemble_per_bar_features`` (patched at the SOURCE it's imported
into — ``backend.algo.paper.runtime`` — per CLAUDE.md rule 16) to
capture the kwarg passed in and the resulting dict from
``PaperRuntime._on_bar_close``'s single call site.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.paper.runtime import PaperRuntime
from backend.algo.strategy.ast import parse_strategy

_TICKER = "TESTPAPERDISTSMA.NS"
_PRICE = Decimal("500.00")
_NEW_KEY = "nifty_distance_from_sma200_pct"


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "buy on every bar",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {
                "ticker_type": ["stock"], "market": "india",
            },
        },
        "schedule": {
            "type": "bar_close", "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 5}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {
                "max_loss_pct": 2,
                "max_open_positions": 10,
            },
        },
    }


def _bare_runtime() -> PaperRuntime:
    return PaperRuntime(
        strategy=parse_strategy(_strategy_payload()),
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=date(2026, 4, 1),
    )


def _make_bar():
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=_TICKER,
        interval_sec=86400,
        bar_open_ts_ns=1_000_000_000,
        open=float(_PRICE),
        high=float(_PRICE),
        low=float(_PRICE),
        close=float(_PRICE),
        volume=1000,
        written_at=datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc),
    )


def test_market_dist_sma200_cache_populated_at_init():
    """``PaperRuntime.__init__`` computes
    ``self._market_dist_sma200`` alongside ``_market_regime`` /
    ``_market_trend`` — the same try/except-guarded regime-cache
    block."""
    runtime = _bare_runtime()
    assert hasattr(runtime, "_market_dist_sma200")
    assert isinstance(runtime._market_dist_sma200, dict)


def test_on_bar_close_assembles_the_new_feature_key():
    """Spy on ``assemble_per_bar_features`` at PaperRuntime's
    call site: the returned ``EvalContext.features`` dict must
    contain ``nifty_distance_from_sma200_pct`` — proving the paper
    path never hits ``missing_feature`` for it."""
    from backend.algo.features.per_bar import (
        assemble_per_bar_features as _real_assemble,
    )

    runtime = _bare_runtime()

    captured: dict = {}

    def _spy(**kwargs):
        result = _real_assemble(**kwargs)
        captured["kwargs"] = kwargs
        captured["features"] = result
        return result

    with patch(
        "backend.algo.paper.runtime.assemble_per_bar_features",
        side_effect=_spy,
    ):
        runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    assert captured, "assemble_per_bar_features was never called"
    assert "market_dist_sma200" in captured["kwargs"], (
        "PaperRuntime's assemble_per_bar_features call site did "
        "not pass market_dist_sma200"
    )
    assert _NEW_KEY in captured["features"], (
        f"{_NEW_KEY} absent from the assembled EvalContext.features "
        f"— a strategy referencing it would hit missing_feature "
        f"forever on the paper path"
    )
