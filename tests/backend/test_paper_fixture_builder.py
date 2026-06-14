import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.algo.paper.fixture_builder as fb
import backend.algo.paper.supervisor as sup
from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.paper.fixture_builder import (
    FixtureBuildResult,
    _entry_fires,
)
from backend.algo.routes.paper import create_paper_router
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


# Simple rsi_2-only entry cond for build-flow tests.
_RSI_ONLY = {"type": "compare", "op": "<=",
             "left": {"feature": "rsi_2"}, "right": {"literal": 5}}


def _patch_build_context(monkeypatch, tmp_path, *, rows, feats_by_date):
    """Wire every external seam build_universe_fixture touches so the
    test exercises the real scan + dense-emit + write path."""
    monkeypatch.setattr(fb, "_user_fixtures_dir", lambda: tmp_path)
    monkeypatch.setattr(fb, "_resolve_universe", lambda uid: ["TCS.NS"])
    monkeypatch.setattr(fb, "_entry_cond_for_v3", lambda: _RSI_ONLY)
    monkeypatch.setattr(fb, "_dense_closes",
                        lambda tks, s, e: {"TCS.NS": rows} if rows else {})
    monkeypatch.setattr(fb, "_load_market_panels", lambda s, e: ({}, {}))
    monkeypatch.setattr(fb, "_load_factor_by_ticker", lambda tks, s, e: {})
    monkeypatch.setattr(fb, "_load_regime_by_date", lambda s, e: {})
    monkeypatch.setattr(fb, "_features_by_date",
                        lambda history, **kw: feats_by_date)


def test_scan_picks_only_firing_dates():
    d1, d2 = date(2026, 6, 2), date(2026, 6, 3)
    feats = {d1: _f(rsi=3), d2: _f(rsi=40)}
    out = fb._scan_trigger_dates(_COND, feats, max_dates=2)
    assert out == [d1]


def test_scan_respects_max_dates():
    d1, d2, d3 = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)
    feats = {d1: _f(), d2: _f(), d3: _f()}
    out = fb._scan_trigger_dates(_COND, feats, max_dates=2)
    assert out == [d2, d3]  # most-recent 2


def test_synth_ticks_form_single_bar():
    """All ticks for a date MUST land in one 60s bucket so the
    resampler emits exactly ONE bar per date (no zero-delta RSI
    pollution from a spurious second bar)."""
    ticks = fb._synth_ticks("TCS.NS", date(2026, 6, 2),
                            close=3500.0, volume=120)
    assert len(ticks) == 3
    for t in ticks:
        Tick.model_validate(t)
    span = ticks[-1]["ts_ns"] - ticks[0]["ts_ns"]
    assert span < 60 * 1_000_000_000
    bucket = 60 * 1_000_000_000
    assert len({t["ts_ns"] // bucket for t in ticks}) == 1
    assert all(t["ticker"] == "TCS.NS" and t["ltp"] == 3500.0
               for t in ticks)


def test_history_from_closes_is_flat():
    rows = [(date(2026, 6, 1), 100.0, 10), (date(2026, 6, 2), 101.0, 20)]
    hist = fb._history_from_closes("TCS.NS", rows)
    assert len(hist) == 2
    b = hist[1]
    assert b.open == b.high == b.low == b.close == Decimal("101.0")
    assert b.ticker == "TCS.NS" and b.volume == 20


def test_build_universe_fixture_emits_dense_bars(tmp_path, monkeypatch):
    """A firing trigger date emits a DENSE series of bars up to and
    including the trigger — not just the trigger date — so the runtime
    can recompute rsi_2 over real history."""
    trigger = date.today() - timedelta(days=5)
    rows = [
        (trigger - timedelta(days=2), 100.0, 30),
        (trigger - timedelta(days=1), 101.0, 30),
        (trigger, 102.0, 30),
        (trigger + timedelta(days=1), 103.0, 30),  # past latest → dropped
    ]
    _patch_build_context(
        monkeypatch, tmp_path,
        rows=rows, feats_by_date={trigger: {"rsi_2": Decimal(3)}},
    )
    res = fb.build_universe_fixture("u1", lookback_days=30)
    assert res.n_tickers == 1
    assert res.n_trigger_dates == 1
    assert res.trigger_tickers == ["TCS.NS"]
    # 3 dense dates (<= trigger) × 3 ticks = 9; post-trigger row dropped.
    assert res.n_ticks == 9
    out = tmp_path / "u1.jsonl"
    lines = [ln for ln in out.read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    assert len(lines) == 9
    Tick.model_validate(json.loads(lines[0]))


def test_build_universe_fixture_empty_universe_400(monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(fb, "_resolve_universe", lambda uid: [])
    with pytest.raises(HTTPException) as ei:
        fb.build_universe_fixture("u1")
    assert ei.value.status_code == 400


def test_build_universe_fixture_no_rows_skips_ticker(tmp_path, monkeypatch):
    """No OHLCV rows for a ticker → skipped, empty fixture."""
    _patch_build_context(
        monkeypatch, tmp_path, rows=[], feats_by_date={},
    )
    res = fb.build_universe_fixture("u1", lookback_days=30)
    assert res.n_ticks == 0
    assert res.n_trigger_dates == 0
    assert res.trigger_tickers == []


def test_build_universe_fixture_no_triggers_empty(tmp_path, monkeypatch):
    """Rows present but nothing fires → empty fixture (valid outcome)."""
    trigger = date.today() - timedelta(days=5)
    rows = [(trigger, 102.0, 30)]
    _patch_build_context(
        monkeypatch, tmp_path,
        rows=rows, feats_by_date={trigger: {"rsi_2": Decimal(40)}},
    )
    res = fb.build_universe_fixture("u1", lookback_days=30)
    assert res.n_trigger_dates == 0
    assert res.n_ticks == 0
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


# ---------------------------------------------------------------------------
# Task 5: POST /v1/algo/paper/fixtures/build endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def endpoint_app():
    app = FastAPI()
    app.include_router(create_paper_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t",
        role="superuser",
    )
    return app


def test_build_fixture_endpoint_happy(monkeypatch, endpoint_app):
    """POST /fixtures/build returns 200 with fixture metadata."""
    fake_result = FixtureBuildResult(
        filename="00000000-0000-0000-0000-000000000001.jsonl",
        n_tickers=3,
        n_trigger_dates=5,
        n_ticks=120,
        trigger_tickers=["SBIN.NS", "INFY.NS", "TCS.NS"],
    )
    with patch(
        "backend.algo.paper.fixture_builder.build_universe_fixture",
        return_value=fake_result,
    ):
        client = TestClient(endpoint_app)
        r = client.post("/v1/algo/paper/fixtures/build", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == fake_result.filename
    assert body["n_tickers"] == 3
    assert body["n_trigger_dates"] == 5
    assert body["n_ticks"] == 120
    assert body["trigger_tickers"] == ["SBIN.NS", "INFY.NS", "TCS.NS"]


def test_build_fixture_endpoint_custom_lookback(
    monkeypatch, endpoint_app,
):
    """lookback_days is forwarded to build_universe_fixture."""
    fake_result = FixtureBuildResult(
        filename="00000000-0000-0000-0000-000000000001.jsonl",
        n_tickers=1,
        n_trigger_dates=2,
        n_ticks=10,
        trigger_tickers=["HDFCBANK.NS"],
    )
    captured = {}

    def _stub(user_id, *, lookback_days):
        captured["lookback_days"] = lookback_days
        return fake_result

    with patch(
        "backend.algo.paper.fixture_builder.build_universe_fixture",
        side_effect=_stub,
    ):
        client = TestClient(endpoint_app)
        r = client.post(
            "/v1/algo/paper/fixtures/build",
            json={"lookback_days": 90},
        )
    assert r.status_code == 200
    assert captured["lookback_days"] == 90


def test_build_fixture_endpoint_empty_universe_400(
    monkeypatch, endpoint_app,
):
    """Empty universe raises HTTPException(400) surfaced by FastAPI."""
    from fastapi import HTTPException as FastAPIHTTPException

    with patch(
        "backend.algo.paper.fixture_builder.build_universe_fixture",
        side_effect=FastAPIHTTPException(
            status_code=400,
            detail="No universe tickers found",
        ),
    ):
        client = TestClient(endpoint_app)
        r = client.post("/v1/algo/paper/fixtures/build", json={})
    assert r.status_code == 400


def test_build_fixture_endpoint_rejects_out_of_range_lookback(
    endpoint_app,
):
    """lookback_days outside [5, 250] yields 422 validation error."""
    client = TestClient(endpoint_app)
    r = client.post(
        "/v1/algo/paper/fixtures/build",
        json={"lookback_days": 999},
    )
    assert r.status_code == 422


def test_build_fixture_endpoint_rejects_extra_fields(endpoint_app):
    """Extra fields are forbidden (ConfigDict extra='forbid')."""
    client = TestClient(endpoint_app)
    r = client.post(
        "/v1/algo/paper/fixtures/build",
        json={"lookback_days": 30, "unknown_field": "bad"},
    )
    assert r.status_code == 422
