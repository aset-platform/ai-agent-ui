"""ASETPLTFRM — a ticker added to allowed_tickers mid-run, but
absent from stocks.universe_snapshot (checked via
self._bucket_by_ticker), was permanently marked untradeable and
never got its daily-bar history preloaded.

Root cause: found 2026-07-04 investigating recurring
signal_rejected reason=missing_feature missing_key="rsi_2" events
in production for AHLUCONT.NS / MOVALUE.NS / PRUDENT.NS /
SMALLCAP.NS (5111 events over 5 trading days, all on the same live
strategy). None of the 4 tickers exist in stocks.universe_snapshot
(confirmed via direct query — zero rows), so
LiveRuntime._on_bar_close's "skip expensive Kite preload for
LTP-only tokens" optimization (backend/algo/live/runtime.py ~L3455)
treated them as untradeable index-like instruments and permanently
set _bars_by_ticker[ticker] = [] on the very first bar — before
this fix, the check only exempted tickers already in
_bucket_by_ticker or already an open position, never checking
whether the ticker was in the user's own allowed_tickers list. Since
the gate only fires once per ticker (guarded by
``if history is None``), the poisoning was permanent for the
runtime's lifetime — compute_indicators([]) returns {}, so every
bar-derived feature (rsi_2 included) stayed absent forever, even
though a fresh stocks.ohlcv read for these tickers has hundreds of
bars available.

Runtime construction mirrors test_per_bar_offload.py /
test_balance_cap.py (construct with I/O deps mocked, seed
_bucket_by_ticker / _caps directly, drive
await runtime._on_bar_close(...)).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason=(
        "Requires pyarrow + Python >=3.10 "
        "(run inside Docker backend container)"
    ),
)

_ORIGINAL_TICKER = "ORIGINAL.NS"
_NEW_TICKER = "ADDEDMIDRUN.NS"
_PRICE = Decimal("500.00")


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "bucket gate test strategy",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close",
            "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 1}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_bar(ticker: str):
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=ticker,
        interval_sec=86400,
        bar_open_ts_ns=1_000_000_000,
        open=float(_PRICE),
        high=float(_PRICE),
        low=float(_PRICE),
        close=float(_PRICE),
        volume=1000,
        written_at=datetime(2026, 7, 4, 10, 0, tzinfo=timezone.utc),
    )


def _make_runtime():
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_strategy_payload())

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("10000000"),
        "max_orders_per_day": 100,
        "allowed_tickers": [_ORIGINAL_TICKER],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    kite = MagicMock()
    kite.dry_run = False
    kite.place_order = MagicMock(return_value="KITE_ORDER_TEST")

    # allowed_tickers at __init__ time — only the original ticker.
    # _NEW_TICKER is added to self._caps AFTER construction, below,
    # to simulate a mid-run caps edit (PUT /algo/live/caps/{id}).
    caps = {
        "live_orders_enabled": True,
        "allowed_tickers": [_ORIGINAL_TICKER],
    }

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ), patch(
        "backend.algo.live.runtime.LiveRuntime._load_bucket_by_ticker",
        return_value={},
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 7, 4),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


@pytest.mark.asyncio
async def test_ticker_added_mid_run_still_gets_preloaded():
    """A ticker absent from stocks.universe_snapshot (so absent
    from _bucket_by_ticker) but present in the CURRENT
    allowed_tickers must still attempt a real preload, not get
    silently marked untradeable forever."""
    runtime = _make_runtime()
    assert runtime._bucket_by_ticker == {}
    assert _NEW_TICKER not in runtime._caps.get("allowed_tickers", [])

    # Simulate the mid-run caps edit that added this ticker.
    runtime._caps["allowed_tickers"] = [_ORIGINAL_TICKER, _NEW_TICKER]

    fake_bars = {_NEW_TICKER: []}  # preload_daily_bars own return shape
    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
        return_value=fake_bars,
    ) as mock_preload:
        await runtime._on_bar_close(
            bar=_make_bar(_NEW_TICKER), last_price=_PRICE,
        )

    mock_preload.assert_called_once()
    called_tickers = mock_preload.call_args.args[0]
    assert _NEW_TICKER in called_tickers, (
        "preload_daily_bars was never called for a ticker present in "
        "allowed_tickers but absent from _bucket_by_ticker — it was "
        "silently marked untradeable instead of attempting a real "
        "preload."
    )


@pytest.mark.asyncio
async def test_ticker_truly_outside_universe_and_allowlist_still_skipped():
    """Control case — a ticker that is NEITHER in _bucket_by_ticker
    NOR in allowed_tickers NOR an open position (e.g. a pure index
    LTP-subscription token) must still be skipped, preserving the
    original optimization's intent."""
    runtime = _make_runtime()
    untradeable_token = "NIFTY_INDEX_TOKEN"

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
    ) as mock_preload:
        await runtime._on_bar_close(
            bar=_make_bar(untradeable_token), last_price=_PRICE,
        )

    mock_preload.assert_not_called()
    assert runtime._bars_by_ticker[untradeable_token] == []


def _rsi_condition_strategy_payload() -> dict:
    """Same shape as _strategy_payload() but with an AST condition
    that references a bar-derived feature (rsi_2), so eval_node
    actually raises KeyError when features are empty — the bare
    {"type": "buy"} root in _strategy_payload() never exercises that
    path at all."""
    payload = _strategy_payload()
    payload["root"] = {
        "type": "if",
        "cond": {
            "type": "compare",
            "op": "<=",
            "left": {"feature": "rsi_2"},
            "right": {"literal": 5},
        },
        "then": {"type": "buy", "qty": {"shares": 1}},
        "else": {"type": "hold"},
    }
    return payload


def _make_runtime_with_payload(payload: dict):
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(payload)

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("10000000"),
        "max_orders_per_day": 100,
        "allowed_tickers": [],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    kite = MagicMock()
    kite.dry_run = False
    kite.place_order = MagicMock(return_value="KITE_ORDER_TEST")

    caps = {"live_orders_enabled": True, "allowed_tickers": []}

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ), patch(
        "backend.algo.live.runtime.LiveRuntime._load_bucket_by_ticker",
        return_value={},
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 7, 4),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


@pytest.mark.asyncio
async def test_out_of_scope_ticker_never_reaches_eval_on_repeat_bars():
    """ASETPLTFRM-471. A ticker that is NEITHER in _bucket_by_ticker
    NOR in allowed_tickers NOR an open position must never emit
    signal_rejected reason=missing_feature — not on the first bar
    (already covered by the control test above) and NOT on any
    subsequent bar either. Before the fix, only the first bar was
    gated; every bar after that fell through to eval_node and
    emitted a fresh missing_feature event every single cycle."""
    runtime = _make_runtime_with_payload(
        _rsi_condition_strategy_payload(),
    )
    out_of_scope_ticker = "MOVALUE.NS"
    assert out_of_scope_ticker not in runtime._bucket_by_ticker
    assert out_of_scope_ticker not in (
        runtime._caps.get("allowed_tickers") or []
    )

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
    ) as mock_preload:
        # First bar.
        result_1 = await runtime._on_bar_close(
            bar=_make_bar(out_of_scope_ticker), last_price=_PRICE,
        )
        # Second bar for the SAME ticker — this is the case that
        # was broken: history is no longer None, so the old gate
        # (nested inside `if history is None:`) never re-ran.
        result_2 = await runtime._on_bar_close(
            bar=_make_bar(out_of_scope_ticker), last_price=_PRICE,
        )

    assert result_1 == 0
    assert result_2 == 0
    mock_preload.assert_not_called()
    # event_row() stores the payload as a JSON string under
    # "payload_json", not a nested "payload" dict — see
    # debugging-event-row-payload-json-not-payload. A naive
    # e.get("payload", {}) filter would silently match nothing.
    missing_feature_events = [
        e for e in runtime._events
        if e.get("type") == "signal_rejected"
        and json.loads(e.get("payload_json", "{}")).get("reason")
        == "missing_feature"
    ]
    assert missing_feature_events == [], (
        "out-of-scope ticker emitted signal_rejected "
        "reason=missing_feature — the per-bar short-circuit did not "
        "fire on a repeat bar-close"
    )
