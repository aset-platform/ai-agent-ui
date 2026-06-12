import json
from datetime import date
from decimal import Decimal

import pytest

import backend.algo.paper.fixture_builder as fb
import backend.algo.paper.supervisor as sup
from backend.algo.paper.fixture_builder import _entry_fires
from backend.algo.stream.types import Tick

_COND = {"type": "and", "operands": [
    {"type": "compare", "op": "<=", "left": {"feature": "rsi_2"},
     "right": {"literal": 5}},
    {"type": "compare", "op": ">", "left": {"feature": "distance_from_sma200"},
     "right": {"literal": 0}},
    {"type": "compare", "op": "<", "left": {"feature": "stress_prob"},
     "right": {"literal": 0.5}},
    {"type": "compare", "op": ">=", "left": {"feature": "nifty_above_sma200"},
     "right": {"literal": 1}},
    {"type": "compare", "op": ">", "left": {"feature": "nifty_30d_return_pct"},
     "right": {"literal": -5}},
]}


def _f(rsi=3, dist=5, stress=0.2, nabove=1, n30=2.0):
    return {"rsi_2": Decimal(str(rsi)),
            "distance_from_sma200": Decimal(str(dist)),
            "stress_prob": Decimal(str(stress)),
            "nifty_above_sma200": Decimal(str(nabove)),
            "nifty_30d_return_pct": Decimal(str(n30))}


def test_entry_fires_all_gates_pass():
    assert _entry_fires(_COND, _f()) is True


def test_entry_fires_false_rsi_high():
    assert _entry_fires(_COND, _f(rsi=40)) is False


def test_entry_fires_false_missing_feature():
    assert _entry_fires(_COND, {}) is False  # KeyError swallowed


def test_scan_picks_only_firing_dates(monkeypatch):
    d1, d2 = date(2026, 6, 2), date(2026, 6, 3)
    monkeypatch.setattr(
        fb, "_assembled_features_by_date",
        lambda ticker, start, end: {d1: _f(rsi=3), d2: _f(rsi=40)},
    )
    out = fb._scan_trigger_dates(
        "TCS.NS", _COND, date(2026, 6, 1), date(2026, 6, 4),
        max_dates=2,
    )
    assert out == [d1]


def test_scan_respects_max_dates(monkeypatch):
    d1, d2, d3 = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)
    monkeypatch.setattr(
        fb, "_assembled_features_by_date",
        lambda t, s, e: {d1: _f(), d2: _f(), d3: _f()},
    )
    out = fb._scan_trigger_dates(
        "TCS.NS", _COND, d1, d3, max_dates=2,
    )
    assert out == [d2, d3]  # most-recent 2


def test_synth_ticks_close_a_bar():
    ticks = fb._synth_ticks("TCS.NS", date(2026, 6, 2),
                            close=3500.0, volume=120)
    assert len(ticks) >= 2
    for t in ticks:
        Tick.model_validate(t)
    span = ticks[-1]["ts_ns"] - ticks[0]["ts_ns"]
    assert span >= 60 * 1_000_000_000
    assert all(t["ticker"] == "TCS.NS" and t["ltp"] == 3500.0
               for t in ticks)


def test_build_universe_fixture_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "_user_fixtures_dir", lambda: tmp_path)
    monkeypatch.setattr(fb, "_resolve_universe", lambda uid: ["TCS.NS"])
    monkeypatch.setattr(fb, "_entry_cond_for_v3",
                        lambda: {"type": "compare", "op": "<=",
                                 "left": {"feature": "rsi_2"},
                                 "right": {"literal": 5}})
    monkeypatch.setattr(fb, "_scan_trigger_dates",
                        lambda t, c, s, e, *, max_dates: [date(2026, 6, 2)])
    monkeypatch.setattr(fb, "_close_for", lambda t, dt: (3500.0, 100))
    res = fb.build_universe_fixture("u1", lookback_days=30)
    assert res.n_tickers == 1 and res.n_trigger_dates == 1
    assert res.n_ticks >= 2
    out = tmp_path / "u1.jsonl"
    lines = [ln for ln in out.read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    Tick.model_validate(json.loads(lines[0]))


def test_build_universe_fixture_empty_universe_400(monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(fb, "_resolve_universe", lambda uid: [])
    with pytest.raises(HTTPException) as ei:
        fb.build_universe_fixture("u1")
    assert ei.value.status_code == 400


def test_build_universe_fixture_skips_none_close(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "_user_fixtures_dir", lambda: tmp_path)
    monkeypatch.setattr(fb, "_resolve_universe", lambda uid: ["TCS.NS"])
    monkeypatch.setattr(fb, "_entry_cond_for_v3",
                        lambda: {"type": "compare", "op": "<=",
                                 "left": {"feature": "rsi_2"},
                                 "right": {"literal": 5}})
    monkeypatch.setattr(fb, "_scan_trigger_dates",
                        lambda t, c, s, e, *, max_dates: [date(2026, 6, 2)])
    monkeypatch.setattr(fb, "_close_for", lambda t, dt: (None, 0))
    res = fb.build_universe_fixture("u1", lookback_days=30)
    assert res.n_ticks == 0
    assert res.n_trigger_dates == 0
    assert res.trigger_tickers == []


# ---------------------------------------------------------------------------
# Task 4: replay loader user-dir allowlist
# ---------------------------------------------------------------------------


def test_build_replay_source_allows_user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "_USER_FIXTURES_ROOT", tmp_path.resolve())
    (tmp_path / "u1.jsonl").write_text(
        '{"ticker":"X.NS","ts_ns":0,"ltp":1.0,"volume":1}\n')
    assert sup.build_replay_source("u1.jsonl", user_id="u1") is not None


def test_build_replay_source_rejects_other_users_fixture(
    tmp_path, monkeypatch
):
    """Cross-tenant access must be blocked (IDOR)."""
    ci_root = tmp_path / "ci"
    ci_root.mkdir()
    user_root = tmp_path / "users"
    user_root.mkdir()
    monkeypatch.setattr(sup, "_FIXTURES_ROOT", ci_root.resolve())
    monkeypatch.setattr(sup, "_USER_FIXTURES_ROOT", user_root.resolve())
    (user_root / "other.jsonl").write_text(
        '{"ticker":"X.NS","ts_ns":0,"ltp":1.0,"volume":1}\n')
    with pytest.raises(ValueError):
        sup.build_replay_source("other.jsonl", user_id="u1")


def test_build_replay_source_rejects_traversal():
    with pytest.raises(ValueError):
        sup.build_replay_source("../../../../etc/passwd")


def test_list_replay_fixtures_scopes_user_dir(tmp_path, monkeypatch):
    """list_replay_fixtures must expose only the caller's file,
    not other users' files in the user fixtures root."""
    monkeypatch.setattr(sup, "_USER_FIXTURES_ROOT", tmp_path.resolve())
    monkeypatch.setattr(
        sup, "_FIXTURES_ROOT", tmp_path / "_ci_empty_nonexistent",
    )
    (tmp_path / "u1.jsonl").write_text(
        '{"ticker":"X.NS","ts_ns":0,"ltp":1.0,"volume":1}\n')
    (tmp_path / "other.jsonl").write_text(
        '{"ticker":"Y.NS","ts_ns":0,"ltp":2.0,"volume":1}\n')

    result = sup.list_replay_fixtures(user_id="u1")

    names = [r["path"] for r in result]
    assert "u1.jsonl" in names
    assert "other.jsonl" not in names
