"""REGIME-3 regime-change daily notifier tests.

Diffs today's vs yesterday's regime label (from
``stocks.regime_history``) and emits exactly one ``regime_changed``
event into ``algo.events`` on a flip.  No per-user fan-out — the
frontend banner is driven by ``useRegimeCurrent`` polling +
localStorage diff.
"""
from __future__ import annotations

from datetime import date

from backend.algo.jobs import regime_change_notifier as mod


def test_no_event_when_regime_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "_get_regime_for_date", lambda d: "BULL",
    )
    captured: list = []
    monkeypatch.setattr(
        mod, "_emit_event", lambda **kw: captured.append(kw),
    )
    out = mod.run_notifier(as_of=date(2026, 5, 10))
    assert out is None
    assert captured == []


def test_event_emitted_on_flip(monkeypatch) -> None:
    def regime_for(d: date) -> str | None:
        return "SIDEWAYS" if d == date(2026, 5, 10) else "BULL"

    monkeypatch.setattr(mod, "_get_regime_for_date", regime_for)
    captured: list = []
    monkeypatch.setattr(
        mod, "_emit_event", lambda **kw: captured.append(kw),
    )
    out = mod.run_notifier(as_of=date(2026, 5, 10))
    assert out is not None
    assert out["from_regime"] == "BULL"
    assert out["to_regime"] == "SIDEWAYS"
    assert out["bar_date"] == "2026-05-10"
    assert len(captured) == 1
    assert captured[0]["payload"]["from_regime"] == "BULL"
    assert captured[0]["payload"]["to_regime"] == "SIDEWAYS"


def test_no_event_when_yesterday_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "_get_regime_for_date",
        lambda d: "BULL" if d == date(2026, 5, 10) else None,
    )
    captured: list = []
    monkeypatch.setattr(
        mod, "_emit_event", lambda **kw: captured.append(kw),
    )
    out = mod.run_notifier(as_of=date(2026, 5, 10))
    assert out is None
    assert captured == []


def test_no_event_when_today_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "_get_regime_for_date",
        lambda d: None if d == date(2026, 5, 10) else "BULL",
    )
    captured: list = []
    monkeypatch.setattr(
        mod, "_emit_event", lambda **kw: captured.append(kw),
    )
    out = mod.run_notifier(as_of=date(2026, 5, 10))
    assert out is None
    assert captured == []
