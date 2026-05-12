"""Smoke tests for /v1/algo/attribution/* endpoints (REGIME-6)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.attribution import (
    create_attribution_router,
)

USER_ID = "00000000-0000-0000-0000-000000000001"


def _mappings_result(rows: list[dict]) -> MagicMock:
    """Mimic SQLAlchemy result.mappings().all() shape."""
    fake_mappings = MagicMock()
    fake_mappings.all.return_value = rows
    fake_mappings.first.return_value = rows[0] if rows else None
    fake_result = MagicMock()
    fake_result.mappings.return_value = fake_mappings
    return fake_result


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(
        create_attribution_router(), prefix="/v1",
    )
    app.dependency_overrides[pro_or_superuser] = (
        lambda: UserContext(
            user_id=USER_ID, email="t@t", role="superuser",
        )
    )

    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    # Build an async context manager class so
    #   async with factory() as session:
    # binds `session` to our fake_session.
    class _CM:
        async def __aenter__(self_inner):  # noqa: N805
            return fake_session

        async def __aexit__(self_inner, *a):  # noqa: N805
            return None

    def _factory_call():
        return _CM()

    factory_factory = MagicMock(return_value=_factory_call)

    import backend.algo.routes.attribution as routes_mod
    monkeypatch.setattr(
        routes_mod, "_get_session_factory", factory_factory,
    )

    # Disable cache so each test exercises the underlying query
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    fake_cache.set.return_value = None
    monkeypatch.setattr(routes_mod, "get_cache", lambda: fake_cache)

    app.state.fake_session = fake_session
    return app


def test_daily_returns_empty_when_no_rows(app) -> None:
    app.state.fake_session.execute = AsyncMock(
        return_value=_mappings_result([]),
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/daily")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"rows": [], "total": 0}


def test_daily_serialises_jsonb_columns(app) -> None:
    """JSONB columns may surface as dict (asyncpg) or as JSON
    string (SQLAlchemy text). Both must serialise out as dict."""
    app.state.fake_session.execute = AsyncMock(
        return_value=_mappings_result([
            {
                "user_id": USER_ID,
                "strategy_id": "00000000-0000-0000-0000-0000000000aa",
                "bar_date": date(2026, 5, 9),
                "brinson_alloc": {"IT": 0.01},
                "brinson_select": '{"IT": -0.005}',  # str variant
                "brinson_interaction": None,
                "total_active_return": 0.005,
                "created_at": datetime(
                    2026, 5, 9, 12, 0, tzinfo=timezone.utc,
                ),
            },
        ]),
    )
    client = TestClient(app)
    r = client.get(
        "/v1/algo/attribution/daily?strategy_id="
        "00000000-0000-0000-0000-0000000000aa",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    row = body["rows"][0]
    assert row["bar_date"] == "2026-05-09"
    assert row["brinson_alloc"] == {"IT": 0.01}
    assert row["brinson_select"] == {"IT": -0.005}
    assert row["brinson_interaction"] == {}
    assert row["total_active_return"] == 0.005
    assert row["created_at"].startswith("2026-05-09")


def test_regression_strips_mock_flag_into_metadata(app) -> None:
    """The persisted row carries betas['__mock_data__']=1.0 — the
    route MUST surface it as a top-level mock_data:bool and
    strip it from the betas dict so the UI plot doesn't render
    a phantom factor."""
    app.state.fake_session.execute = AsyncMock(
        return_value=_mappings_result([
            {
                "user_id": USER_ID,
                "strategy_id": "00000000-0000-0000-0000-0000000000bb",
                "period_start": date(2026, 4, 1),
                "period_end": date(2026, 5, 1),
                "alpha": 0.0003,
                "betas": {
                    "MKT": 0.8, "SMB": 0.3, "HML": -0.1,
                    "MOM": 0.05, "__mock_data__": 1.0,
                },
                "r_squared": 0.97,
                "n_observations": 30,
                "created_at": datetime(
                    2026, 5, 2, 0, 0, tzinfo=timezone.utc,
                ),
            },
        ]),
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/regression")
    assert r.status_code == 200, r.text
    body = r.json()
    row = body["rows"][0]
    assert row["mock_data"] is True
    assert "__mock_data__" not in row["betas"]
    assert row["betas"] == {
        "MKT": 0.8, "SMB": 0.3, "HML": -0.1, "MOM": 0.05,
    }
    assert row["alpha"] == pytest.approx(0.0003)
    assert row["n_observations"] == 30


def test_trades_pairs_buy_sell_signals_via_fills(
    app, monkeypatch,
) -> None:
    """ASETPLTFRM-381 — pairing now bucketed by the canonical
    symbol (``RELIANCE``, no ``.NS`` suffix). Signals carry
    ``payload.ticker`` (with ``.NS``); fills carry
    ``payload.symbol`` (without). The route normalises both into
    the same bucket so the pair surfaces."""
    base_ts = int(
        datetime(2026, 5, 10, 9, 30, tzinfo=timezone.utc)
        .timestamp() * 1_000_000_000,
    )
    fake_events = [
        {
            "user_id": USER_ID,
            "strategy_id": "ssss",
            "type": "signal_generated",
            "payload_json": (
                '{"ticker": "RELIANCE.NS", "side": "BUY", '
                '"qty": 10, "regime_label": "BULL", '
                '"factor_exposures": {"mom_12_1": 0.8}}'
            ),
            "ts_ns": base_ts,
        },
        {
            "user_id": USER_ID,
            "strategy_id": "ssss",
            "type": "order_filled",
            "payload_json": (
                '{"symbol": "RELIANCE", "side": "BUY", '
                '"qty": 10, "fill_price": 1234.5}'
            ),
            "ts_ns": base_ts + 1,
        },
        {
            "user_id": USER_ID,
            "strategy_id": "ssss",
            "type": "signal_generated",
            "payload_json": (
                '{"ticker": "RELIANCE.NS", "side": "SELL", '
                '"qty": 10, "reason": "trailing_stop"}'
            ),
            "ts_ns": base_ts + 100,
        },
        {
            "user_id": USER_ID,
            "strategy_id": "ssss",
            "type": "order_filled",
            "payload_json": (
                '{"symbol": "RELIANCE", "side": "SELL", '
                '"qty": 10, "fill_price": 1456.0}'
            ),
            "ts_ns": base_ts + 101,
        },
    ]
    monkeypatch.setattr(
        "backend.db.duckdb_engine.query_iceberg_table",
        lambda table, sql, params=None: fake_events,
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/trades")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    row = body["rows"][0]
    # Canonical symbol form: ``.NS`` stripped.
    assert row["ticker"] == "RELIANCE"
    assert row["entry_price"] == 1234.5
    assert row["exit_price"] == 1456.0
    assert row["qty"] == 10
    assert row["entry_regime"] == "BULL"
    assert row["exit_reason"] == "trailing_stop"
    assert "BULL" in row["reason_text"]
    assert "trailing_stop" in row["reason_text"]


def test_trades_panic_close_pairs_without_sell_signal(
    app, monkeypatch,
) -> None:
    """ASETPLTFRM-381 — when panic close fills a SELL without a
    matching signal_generated, the pair still surfaces. Today's
    live session: BUY fill at 11:41 + Panic Close SELL fill at
    12:25 → one trade row with exit_reason resolved from the
    synthetic panic_close signal."""
    base_ts = int(
        datetime(2026, 5, 12, 6, 11, tzinfo=timezone.utc)
        .timestamp() * 1_000_000_000,
    )
    fake_events = [
        # BUY: full signal → fill
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "signal_generated", "ts_ns": base_ts,
            "payload_json": (
                '{"ticker": "ITC.NS", "side": "BUY", '
                '"qty": 4, "regime_label": "BULL"}'
            ),
        },
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live", "ts_ns": base_ts + 1,
            "payload_json": (
                '{"symbol": "ITC", "side": "BUY", "qty": 4, '
                '"fill_price": 304.05}'
            ),
        },
        # Panic close synthetic signal_generated (ASETPLTFRM-381)
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "signal_generated", "ts_ns": base_ts + 100,
            "payload_json": (
                '{"symbol": "ITC", "ticker": "ITC.NS", '
                '"side": "SELL", "qty": 4, '
                '"reason": "panic_close", '
                '"exit_reason": "panic_close"}'
            ),
        },
        # SELL fill from Kite postback
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live", "ts_ns": base_ts + 101,
            "payload_json": (
                '{"symbol": "ITC", "side": "SELL", "qty": 4, '
                '"fill_price": 304.70}'
            ),
        },
    ]
    monkeypatch.setattr(
        "backend.db.duckdb_engine.query_iceberg_table",
        lambda table, sql, params=None: fake_events,
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/trades")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    row = body["rows"][0]
    assert row["ticker"] == "ITC"
    assert row["qty"] == 4
    assert row["entry_price"] == 304.05
    assert row["exit_price"] == 304.70
    assert row["exit_reason"] == "panic_close"


def test_trades_pairs_when_no_signals_at_all(
    app, monkeypatch,
) -> None:
    """Bare fills (no signals on either side — manual order
    placement) should still pair into a trade row with
    exit_reason=None. Avoids the pre-381 false-empty case."""
    base_ts = int(
        datetime(2026, 5, 12, 6, 11, tzinfo=timezone.utc)
        .timestamp() * 1_000_000_000,
    )
    fake_events = [
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live", "ts_ns": base_ts,
            "payload_json": (
                '{"symbol": "TCS", "side": "BUY", "qty": 1, '
                '"fill_price": 4000.0}'
            ),
        },
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live", "ts_ns": base_ts + 1,
            "payload_json": (
                '{"symbol": "TCS", "side": "SELL", "qty": 1, '
                '"fill_price": 4050.0}'
            ),
        },
    ]
    monkeypatch.setattr(
        "backend.db.duckdb_engine.query_iceberg_table",
        lambda table, sql, params=None: fake_events,
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/trades")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    row = body["rows"][0]
    assert row["ticker"] == "TCS"
    assert row["entry_price"] == 4000.0
    assert row["exit_price"] == 4050.0
    # No entry signal → no regime / exposures, but pair still
    # surfaces.
    assert row["entry_regime"] is None
    assert row["exit_reason"] is None


def test_trades_returns_empty_when_no_events(
    app, monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.db.duckdb_engine.query_iceberg_table",
        lambda table, sql, params=None: [],
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/trades")
    assert r.status_code == 200
    assert r.json()["rows"] == []


def test_unauthenticated_request_returns_401() -> None:
    """Without the dependency override the real pro_or_superuser
    guard kicks in — a plain GET 401s."""
    app = FastAPI()
    app.include_router(
        create_attribution_router(), prefix="/v1",
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/daily")
    assert r.status_code in (401, 403)
