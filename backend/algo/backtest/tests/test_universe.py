"""Tests for resolve_universe is_fno post-filter."""

from types import SimpleNamespace

import pytest

from backend.algo.backtest import universe as uni

# Fake registry: all test tickers are classified as "stock" so
# _apply_filter's ticker_type check doesn't discard them before
# the F&O intersect runs.
_FAKE_REGISTRY = {
    "RELIANCE.NS": {"ticker_type": "stock"},
    "HDFCBANK.NS": {"ticker_type": "stock"},
    "INFY.NS":     {"ticker_type": "stock"},
    "OBSCURE.NS":  {"ticker_type": "stock"},
}


class _StrategyStub:
    """Minimal stand-in for parsed Strategy with universe.filter."""

    def __init__(self, *, is_fno: bool):
        self.universe = SimpleNamespace(
            scope="discovery",
            filter=SimpleNamespace(
                ticker_type=["stock"],
                market="india",
                is_fno=is_fno,
            ),
        )


@pytest.mark.asyncio
async def test_resolve_universe_filters_to_fno_when_is_fno_true(
    monkeypatch,
):
    """is_fno=True should intersect candidates with fno_200.csv."""
    candidates = [
        "RELIANCE.NS", "HDFCBANK.NS", "INFY.NS",
        "OBSCURE.NS",   # NOT in F&O list
    ]

    async def fake_scoped(*, user, scope):
        return candidates

    monkeypatch.setattr(uni, "_scoped_tickers", fake_scoped)
    monkeypatch.setattr(uni, "_registry_meta", lambda: _FAKE_REGISTRY)

    def fake_fno_universe():
        return ["RELIANCE.NS", "HDFCBANK.NS", "INFY.NS"]

    monkeypatch.setattr(
        "backend.algo.research.intraday_15m_mis_bakeoff.universe."
        "load_fno_universe",
        fake_fno_universe,
    )

    out = await uni.resolve_universe(
        user=SimpleNamespace(),
        strategy=_StrategyStub(is_fno=True),
    )
    assert set(out) == {"RELIANCE.NS", "HDFCBANK.NS", "INFY.NS"}
    assert "OBSCURE.NS" not in out


@pytest.mark.asyncio
async def test_resolve_universe_unchanged_when_is_fno_false(
    monkeypatch,
):
    """is_fno=False preserves the existing behaviour exactly."""
    candidates = ["RELIANCE.NS", "OBSCURE.NS"]

    async def fake_scoped(*, user, scope):
        return candidates

    monkeypatch.setattr(uni, "_scoped_tickers", fake_scoped)
    monkeypatch.setattr(uni, "_registry_meta", lambda: _FAKE_REGISTRY)

    out = await uni.resolve_universe(
        user=SimpleNamespace(),
        strategy=_StrategyStub(is_fno=False),
    )
    assert set(out) == set(candidates)
