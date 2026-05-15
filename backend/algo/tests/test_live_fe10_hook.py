"""FE-10 hook test: LiveRuntime calls
:func:`emit_features_for_bar` once per closed bar when the
strategy runs an intraday cadence.

Mirrors the FE-5 ``test_live_snapshot_hook`` shape but drives
``_on_bar_close`` directly with a stub minute-bar (no Kite
postback). The emitter is patched so no real Iceberg I/O fires.

Daily-cadence strategies MUST NOT trigger the emitter — FE-3
daily compute owns daily features.
"""

from __future__ import annotations

import importlib
import sys
from datetime import (
    date,
    datetime,
)
from datetime import time as _time
from datetime import (
    timedelta,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.broker.kite_client import KiteClient

_RUNTIME_AVAILABLE = importlib.util.find_spec(
    "pyarrow"
) is not None and sys.version_info >= (3, 10)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires the backend Docker stack (pyarrow + py≥3.10)",
)


def _series(ticker: str, end: date, n: int):
    """N consecutive ascending daily bars ending at ``end``."""
    from backend.algo.backtest.types import BarData

    out = []
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        c = Decimal(str(100 + i))
        out.append(
            BarData(
                ticker=ticker,
                date=d,
                open=c,
                high=c + 1,
                low=c - 1,
                close=c,
                volume=1000,
            )
        )
    return out


def _make_runtime(*, interval: str):
    """LiveRuntime with intraday cadence pinned + intraday warmup
    stubbed. Daily preload is stubbed too so the daily-init path
    is harmless when the test pins an intraday cadence."""
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import Strategy

    strategy = MagicMock(spec=Strategy)
    strategy.id = uuid4()
    strategy.risk = MagicMock()
    strategy.risk.model_dump.return_value = {}
    strategy.root = MagicMock()
    strategy.root.model_dump.return_value = {"type": "hold"}
    strategy.schedule = MagicMock()
    strategy.schedule.interval = interval
    strategy.product = "CNC"
    strategy.entry_cutoff_time = None

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {"live_orders_enabled": True}

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        kite = KiteClient(
            api_key="k",
            access_token="tok",
            dry_run=True,
        )
        kite._kc = kc_instance

    today = date.today()
    payload = {
        "ITC.NS": _series(
            "ITC.NS",
            today - timedelta(days=1),
            250,
        ),
    }
    caps: dict = {
        "live_orders_enabled": True,
        "allowed_tickers": ["ITC.NS"],
    }
    with (
        patch(
            "backend.algo.live.daily_bar_warmup.preload_daily_bars",
            return_value=payload,
        ),
        patch(
            "backend.algo.live.intraday_bar_warmup." "preload_intraday_bars",
            return_value=payload,
        ),
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=None,
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


def _minute_bar(
    ticker: str,
    date_obj: date,
    *,
    close: float = 100.0,
    volume: int = 100,
):
    """Bar stub matching algo.stream.types.Bar's used fields."""
    ts = datetime(
        date_obj.year,
        date_obj.month,
        date_obj.day,
        3,
        45,
        tzinfo=timezone.utc,
    )
    ts_ns = int(ts.timestamp() * 1_000_000_000)
    return SimpleNamespace(
        ticker=ticker,
        bar_open_ts_ns=ts_ns,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


@pytest.mark.asyncio
async def test_live_runtime_calls_fe10_emitter_on_intraday_bar() -> None:
    """Intraday cadence (``15m``) → emitter called with
    ``mode='live'``, the right ``interval_sec`` (900), and the
    cumulative history (warmup-loaded + the just-closed bar)."""
    runtime = _make_runtime(interval="15m")
    bar = _minute_bar("ITC.NS", date.today(), close=301, volume=10)

    with (
        patch(
            "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
            _time(0, 0),
        ),
        patch.object(
            runtime._evaluator,
            "eval_node",
            return_value={"type": "hold"},
        ),
        patch(
            "backend.algo.features.live_emitter." "emit_features_for_bar",
        ) as emit_mock,
    ):
        await runtime._on_bar_close(
            bar=bar,
            last_price=Decimal("301"),
        )

    assert emit_mock.call_count == 1
    kw = emit_mock.call_args.kwargs
    assert kw["mode"] == "live"
    assert kw["cadence_interval"] == "15m"
    assert kw["interval_sec"] == 900
    assert kw["ticker"] == "ITC.NS"
    assert isinstance(kw["history"], list)
    assert kw["history"]


@pytest.mark.asyncio
async def test_live_runtime_does_not_call_fe10_for_daily() -> None:
    """Daily cadence (``1d``) → emitter NEVER called. FE-3
    daily compute owns daily features."""
    runtime = _make_runtime(interval="1d")
    bar = _minute_bar("ITC.NS", date.today(), close=301, volume=10)

    with (
        patch(
            "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
            _time(0, 0),
        ),
        patch.object(
            runtime._evaluator,
            "eval_node",
            return_value={"type": "hold"},
        ),
        patch(
            "backend.algo.features.live_emitter." "emit_features_for_bar",
        ) as emit_mock,
    ):
        await runtime._on_bar_close(
            bar=bar,
            last_price=Decimal("301"),
        )

    emit_mock.assert_not_called()


@pytest.mark.asyncio
async def test_live_runtime_fe10_failure_does_not_break_handler() -> None:
    """Emitter raise MUST NOT propagate — strategy evaluation
    still runs, ``_on_bar_close`` still returns its int."""
    runtime = _make_runtime(interval="15m")
    bar = _minute_bar("ITC.NS", date.today(), close=301, volume=10)

    eval_spy = MagicMock(return_value={"type": "hold"})
    with (
        patch(
            "backend.algo.live.runtime._MIN_EVAL_TIME_IST",
            _time(0, 0),
        ),
        patch.object(runtime._evaluator, "eval_node", eval_spy),
        patch(
            "backend.algo.features.live_emitter." "emit_features_for_bar",
            side_effect=RuntimeError("simulated iceberg outage"),
        ),
    ):
        result = await runtime._on_bar_close(
            bar=bar,
            last_price=Decimal("301"),
        )

    # Handler completed normally; evaluator still fired.
    assert result == 0
    assert eval_spy.call_count == 1
