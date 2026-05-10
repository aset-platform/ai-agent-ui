"""PIT resolver tests (REGIME-7)."""
from __future__ import annotations

from datetime import date


def test_resolve_returns_latest_snapshot(monkeypatch) -> None:
    rows = [
        # snapshot for 2026-04-01
        {"rebalance_date": date(2026, 4, 1), "ticker": "A.NS"},
        {"rebalance_date": date(2026, 4, 1), "ticker": "B.NS"},
        # newer snapshot for 2026-05-01 (must beat the older one)
        {"rebalance_date": date(2026, 5, 1), "ticker": "C.NS"},
        {"rebalance_date": date(2026, 5, 1), "ticker": "D.NS"},
    ]
    from backend.algo.universe import pit_resolver as mod

    monkeypatch.setattr(mod, "_query_snapshot_rows", lambda d: rows)
    out = mod.resolve_pit_universe(date(2026, 5, 15))
    # Latest rebalance <= bar_date is 2026-05-01
    assert sorted(out) == ["C.NS", "D.NS"]


def test_empty_when_no_snapshot(monkeypatch) -> None:
    from backend.algo.universe import pit_resolver as mod

    monkeypatch.setattr(mod, "_query_snapshot_rows", lambda d: [])
    assert mod.resolve_pit_universe(date(2026, 5, 15)) == []


def test_picks_correct_snapshot_at_boundary(monkeypatch) -> None:
    rows = [
        {"rebalance_date": date(2026, 5, 1), "ticker": "X.NS"},
    ]
    from backend.algo.universe import pit_resolver as mod

    monkeypatch.setattr(mod, "_query_snapshot_rows", lambda d: rows)
    # Exactly on rebalance date — included
    assert mod.resolve_pit_universe(date(2026, 5, 1)) == ["X.NS"]


def test_dedups_repeated_tickers(monkeypatch) -> None:
    rows = [
        {"rebalance_date": date(2026, 5, 1), "ticker": "A.NS"},
        {"rebalance_date": date(2026, 5, 1), "ticker": "A.NS"},
        {"rebalance_date": date(2026, 5, 1), "ticker": "B.NS"},
    ]
    from backend.algo.universe import pit_resolver as mod

    monkeypatch.setattr(mod, "_query_snapshot_rows", lambda d: rows)
    assert mod.resolve_pit_universe(date(2026, 5, 1)) == ["A.NS", "B.NS"]
