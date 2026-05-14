"""End-to-end intraday backtest tests (ASETPLTFRM-400 slice 3).

Covers:
- ``BacktestRequest.interval_sec`` field — defaults to daily,
  rejects unsupported cadences.
- Intraday runner dispatch: when ``interval_sec`` ∈ {60, 300,
  900}, the runner calls ``load_intraday_bars_window`` instead
  of ``load_ohlcv_window`` and walks ``(bar_date,
  bar_open_ts_ns)`` tuples.
- T+1 OPEN semantics carry over to intraday: a BUY emitted at
  bar T's close fills at bar T+1's open, NOT at the next
  calendar day's open.
- Daily path unaffected (regression — covered by
  ``test_backtest_runner.py``; this file's daily-vs-intraday
  parity test is a belt-and-braces sanity check).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy

IST = timezone(timedelta(minutes=330))


def _gen_intraday_bars(
    ticker: str,
    *,
    start_day: date = date(2026, 4, 1),
    n_days: int = 5,
    bars_per_day: int = 25,
    interval_sec: int = 900,
) -> list[BarData]:
    """Build a trending-up intraday bar series. Each bar's open
    is the prior bar's close + 1; closes climb monotonically so
    a "buy-and-hold" strategy yields a positive curve."""
    bars: list[BarData] = []
    counter = 0
    for d_off in range(n_days):
        day = start_day + timedelta(days=d_off)
        for bar_idx in range(bars_per_day):
            hour = 9 + (bar_idx * interval_sec) // 3600
            minute = (15 + (bar_idx * interval_sec) // 60) % 60
            open_dt = datetime(
                day.year,
                day.month,
                day.day,
                hour,
                minute,
                tzinfo=IST,
            )
            ts_ns = int(
                open_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000,
            )
            openp = Decimal("100") + Decimal(counter)
            close = openp + Decimal("2")
            bars.append(
                BarData(
                    ticker=ticker,
                    date=day,
                    open=openp,
                    high=close + 1,
                    low=openp - 1,
                    close=close,
                    volume=1000,
                    bar_open_ts_ns=ts_ns,
                )
            )
            counter += 1
    return bars


def _intraday_strategy() -> dict:
    return {
        "id": str(uuid4()),
        "name": "Intraday Buy-5 on every bar (smoke)",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close",
            "interval": "15m",
            "time": "every-bar IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 1}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 50, "max_open_positions": 50},
        },
    }


# ────────────────────────────────────────────────────────────────
# BacktestRequest.interval_sec validation
# ────────────────────────────────────────────────────────────────


def test_default_interval_sec_is_daily():
    """Existing daily callers don't have to update — default
    stays 86400 = daily."""
    req = BacktestRequest(
        strategy_id=uuid4(),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 10),
    )
    assert req.interval_sec == 86400


@pytest.mark.parametrize(
    "supported",
    [60, 300, 900, 86400],
)
def test_supported_interval_sec_accepted(supported):
    req = BacktestRequest(
        strategy_id=uuid4(),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 10),
        interval_sec=supported,
    )
    assert req.interval_sec == supported


@pytest.mark.parametrize(
    "bad",
    [0, 1, 30, 120, 180, 600, 1800, 3600, 7200, 43200],
)
def test_unsupported_interval_sec_rejected(bad):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="not supported"):
        BacktestRequest(
            strategy_id=uuid4(),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 10),
            interval_sec=bad,
        )


# ────────────────────────────────────────────────────────────────
# Intraday runner dispatch + end-to-end smoke
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def intraday_patches():
    """Patch the intraday loader + event flush so the runner
    walks a known synthetic series end-to-end without touching
    Iceberg."""
    bars = {"FAKE.NS": _gen_intraday_bars("FAKE.NS")}
    with (
        patch(
            "backend.algo.backtest.runner.load_intraday_bars_window",
            return_value=bars,
        ) as loader,
        patch(
            "backend.algo.backtest.runner.flush_events",
        ) as flush_mock,
    ):
        yield loader, flush_mock


def test_intraday_dispatch_calls_intraday_loader(intraday_patches):
    """When ``interval_sec=900``, the runner must call
    ``load_intraday_bars_window``, NOT ``load_ohlcv_window``."""
    loader, _ = intraday_patches
    strategy = parse_strategy(_intraday_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 5),
        interval_sec=900,
    )
    run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    loader.assert_called_once()
    assert loader.call_args.kwargs["interval_sec"] == 900


def test_intraday_runner_produces_summary(intraday_patches):
    """5 days × 25 bars × ``buy 1`` strategy → summary returns
    with > 0 fees + a non-empty equity curve. Smoke test only."""
    strategy = parse_strategy(_intraday_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 5),
        interval_sec=900,
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.run_id is not None
    # Each bar issues a BUY; T+1-OPEN fills mean the LAST bar
    # has nowhere to fill so we expect 124 fills (out of 125).
    assert summary.total_fees_inr > Decimal("0")
    assert len(summary.equity_curve) > 0


def test_intraday_fills_at_next_intraday_bar_not_next_day():
    """T+1 OPEN semantics for intraday = NEXT BAR open, not
    next calendar day's open. Verified by inspecting the
    ``order_filled`` events emitted by the runner."""
    bars = {
        "FAKE.NS": _gen_intraday_bars(
            "FAKE.NS",
            n_days=2,
            bars_per_day=3,
        )
    }
    captured_events: list[dict] = []

    def _capture(events):
        captured_events.extend(events)

    strategy = parse_strategy(_intraday_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 2),
        interval_sec=900,
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_intraday_bars_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.flush_events",
            side_effect=_capture,
        ),
    ):
        run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=["FAKE.NS"],
        )

    fills = [e for e in captured_events if e.get("type") == "order_filled"]
    assert len(fills) > 0
    # First two BUYs: bar idx 0 (9:15) fires → fills at bar 1
    # (9:30); bar idx 1 fires → fills at bar 2 (9:45). Both
    # fills happen on the same trading day, NOT on day 2.
    first_fill_payload = fills[0]["payload_json"]
    import json

    first_fill = json.loads(first_fill_payload)
    assert first_fill["fill_date"] == "2026-04-01"


def test_intraday_summary_carries_interval_sec(intraday_patches):
    """ASETPLTFRM-400 slice 7 — UI reads ``summary.interval_sec``
    to render the cadence chip. Pin to 900 for the 15m run so
    the field doesn't silently default to daily on the wire."""
    strategy = parse_strategy(_intraday_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 2),
        interval_sec=900,
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.interval_sec == 900


def test_intraday_equity_curve_carries_bar_open_ts_ns(
    intraday_patches,
):
    """ASETPLTFRM-400 slice 5 — every intraday equity snapshot
    must populate ``bar_open_ts_ns`` so the UI can plot the
    curve with intra-day resolution. Without this, ~25 points
    per ``bar_date`` are indistinguishable on the x-axis."""
    strategy = parse_strategy(_intraday_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 2),
        interval_sec=900,
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert len(summary.equity_curve) > 0
    for point in summary.equity_curve:
        assert (
            point.bar_open_ts_ns is not None
        ), f"intraday EquityPoint missing bar_open_ts_ns: {point}"
        assert point.bar_open_ts_ns > 0
    # Strictly ascending across the curve.
    ts_list = [p.bar_open_ts_ns for p in summary.equity_curve]
    assert ts_list == sorted(ts_list)


def test_daily_equity_curve_leaves_bar_open_ts_ns_none():
    """Daily backtests stay byte-for-byte identical — the new
    ``bar_open_ts_ns`` slot is None for every daily
    EquityPoint."""
    from datetime import timedelta as _td

    daily_bars = {
        "FAKE.NS": [
            BarData(
                ticker="FAKE.NS",
                date=date(2026, 4, 1) + _td(days=i),
                open=Decimal("100") + Decimal(i),
                high=Decimal("101") + Decimal(i),
                low=Decimal("99") + Decimal(i),
                close=Decimal("100") + Decimal(i),
                volume=10_000,
            )
            for i in range(10)
        ]
    }
    strategy = parse_strategy(
        _intraday_strategy()
        | {
            "schedule": {
                "type": "bar_close",
                "interval": "1d",
                "time": "15:25 IST",
            },
        }
    )
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 8),
        # interval_sec defaults to 86400 → daily path
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_ohlcv_window",
            return_value=daily_bars,
        ),
        patch(
            "backend.algo.backtest.runner.flush_events",
        ),
    ):
        summary = run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=["FAKE.NS"],
        )
    assert summary.interval_sec == 86400
    for point in summary.equity_curve:
        assert (
            point.bar_open_ts_ns is None
        ), f"daily EquityPoint should leave bar_open_ts_ns None: {point}"


def test_intraday_runner_walks_every_bar_per_day():
    """Run a 1-day × 25-bar window. The buy-every-bar strategy
    should issue at least 24 fills on that day (last bar can't
    fill — no next bar to fill against)."""
    bars = {
        "FAKE.NS": _gen_intraday_bars(
            "FAKE.NS",
            n_days=1,
            bars_per_day=25,
        )
    }
    captured: list[dict] = []
    strategy = parse_strategy(_intraday_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 1),
        interval_sec=900,
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_intraday_bars_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.flush_events",
            side_effect=lambda evs: captured.extend(evs),
        ),
    ):
        run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=["FAKE.NS"],
        )

    fills = [e for e in captured if e.get("type") == "order_filled"]
    # 25 bars; last bar's BUY has no next bar → no fill.
    # → 24 fills.
    assert len(fills) == 24
