"""Tests for ASETPLTFRM-377 — Live spawn pins dry_run explicitly.

The Live page (mode="live") must NEVER honour the per-user Redis
``algo:dry_run:{user}`` flag set by the Strategies → Dry-run tab.
Conversely, mode="dryrun" must ALWAYS run synthetic regardless of
the same Redis state.

The endpoint also installs a defence-in-depth guard: after
constructing the KiteClient for mode="live", we assert
``kite._dry_run is False`` and emit a 500 if it's anything else.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.routes.paper import create_paper_router


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_paper_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000077",
        email="t@t", role="superuser",
    )
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    factory = MagicMock()
    factory.__aenter__ = AsyncMock(return_value=fake_session)
    factory.__aexit__ = AsyncMock(return_value=None)
    factory_factory = MagicMock(return_value=factory)
    import backend.algo.routes.paper as paper_mod
    monkeypatch.setattr(
        paper_mod, "_get_session_factory", factory_factory,
    )
    return app


def _patched_environment(
    *,
    captured: dict,
    force_dry_run: bool | None = None,
):
    """Patch the third-party machinery the live branch reaches into.

    ``captured`` collects the kwargs the SUT passed to KiteClient so
    each test can assert the dry_run pinning directly.
    ``force_dry_run`` lets us simulate the dry-run-leak guard by
    overriding ``kite._dry_run`` post-construction.
    """
    strategy_id = uuid4()
    fake_strategy = type("S", (), {
        "id": strategy_id, "name": "x", "root": None,
    })()

    fake_caps_repo = MagicMock()
    fake_caps_repo.get = AsyncMock(return_value={
        "live_orders_enabled": True,
        "allowed_tickers": [],
    })

    fake_creds_repo = MagicMock()
    fake_creds_repo.load = AsyncMock(return_value={
        "api_key": "k", "access_token": "tok",
        "access_token_expired": False,
    })

    class FakeKite:
        def __init__(self, *args, **kwargs):
            captured["kite_kwargs"] = kwargs
            # Mirror the real resolution priority: explicit
            # kwarg wins. If a test passes force_dry_run, that
            # simulates a regression where the construction
            # path ignored the explicit kwarg.
            if force_dry_run is not None:
                self._dry_run = force_dry_run
            else:
                self._dry_run = bool(kwargs.get("dry_run", False))
            self._access_token = kwargs.get("access_token")
            self._api_key = kwargs.get("api_key")
            self._kc = MagicMock()

        @property
        def dry_run(self):
            return self._dry_run

    fake_run = MagicMock()
    fake_run.run_id = uuid4()
    fake_runs_repo = MagicMock()
    fake_runs_repo.create_pending = AsyncMock(return_value=fake_run)
    fake_runs_repo.mark_running = AsyncMock()

    sv = MagicMock()
    sv.start_live_run = AsyncMock(return_value={
        "user_id": "00000000-0000-0000-0000-000000000077",
        "strategy_id": str(strategy_id),
        "strategy_name": "x",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    })

    return strategy_id, fake_strategy, fake_caps_repo, \
        fake_creds_repo, FakeKite, fake_runs_repo, sv


def _spawn(
    *,
    app,
    mode: str,
    redis_dry_run_value: str | None = "1",
    force_dry_run: bool | None = None,
):
    captured: dict = {}
    (
        strategy_id, fake_strategy, fake_caps_repo,
        fake_creds_repo, FakeKite, fake_runs_repo, sv,
    ) = _patched_environment(
        captured=captured, force_dry_run=force_dry_run,
    )

    # The Redis dry_run flag should be IGNORED on the live path
    # — but we still set it up so any regression that re-couples
    # to it surfaces immediately as wrong-mode behaviour.
    fake_redis = MagicMock()
    fake_redis.get = MagicMock(return_value=redis_dry_run_value)

    with patch(
        "backend.algo.strategy.repo.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.live.caps_repo.CapsRepo",
        return_value=fake_caps_repo,
    ), patch(
        "backend.algo.broker.credentials_repo."
        "BrokerCredentialsRepo",
        return_value=fake_creds_repo,
    ), patch(
        "backend.algo.broker.kite_client.KiteClient",
        FakeKite,
    ), patch(
        "backend.algo.backtest.runs_repo.BacktestRunsRepo",
        return_value=fake_runs_repo,
    ), patch(
        "backend.algo.paper.supervisor.get_supervisor",
        return_value=sv,
    ), patch(
        "backend.algo.paper.kill_switch_repo.KillSwitchRepo"
    ) as ks_cls, patch(
        "backend.algo.broker.kite_client._read_dry_run_env",
        return_value=False,
    ), patch(
        "backend.algo.redis_async.get_async_redis",
        return_value=fake_redis,
    ), patch(
        "backend.algo.paper.supervisor.build_replay_source",
        return_value=MagicMock(),
    ):
        ks_cls.return_value.is_active = AsyncMock(
            return_value=False,
        )
        client = TestClient(app)
        r = client.post(
            "/v1/algo/paper/runs",
            json={
                "strategy_id": str(strategy_id),
                "fixture_path": "ticks_sample.jsonl",
                "source": "replay",
                "mode": mode,
            },
        )

    return r, captured, sv


def test_live_mode_pins_dry_run_false_ignoring_redis_flag(app):
    """mode='live' + Redis flag armed → KiteClient(dry_run=False)."""
    r, captured, sv = _spawn(
        app=app, mode="live", redis_dry_run_value="1",
    )
    assert r.status_code == 201, r.text
    kwargs = captured["kite_kwargs"]
    assert kwargs.get("dry_run") is False, (
        "Live spawn must explicitly pin dry_run=False"
    )
    assert "user_id" not in kwargs, (
        "Live spawn must NOT pass user_id (that would re-enable "
        "Redis dry_run resolution)"
    )
    sv.start_live_run.assert_awaited_once()


def test_dryrun_mode_pins_dry_run_true_regardless_of_redis(app):
    """mode='dryrun' + Redis flag empty → KiteClient(dry_run=True)."""
    r, captured, sv = _spawn(
        app=app, mode="dryrun", redis_dry_run_value=None,
    )
    assert r.status_code == 201, r.text
    kwargs = captured["kite_kwargs"]
    assert kwargs.get("dry_run") is True, (
        "Dryrun spawn must explicitly pin dry_run=True"
    )
    sv.start_live_run.assert_awaited_once()


def test_dry_run_leak_on_live_path_returns_500(app):
    """Defence-in-depth: a regression that leaves _dry_run=True on
    the Live KiteClient must fail closed with 500 before spawn."""
    r, _captured, sv = _spawn(
        app=app, mode="live", force_dry_run=True,
    )
    assert r.status_code == 500
    assert "dry-run leak" in r.json().get("detail", "")
    sv.start_live_run.assert_not_awaited()
