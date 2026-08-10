"""PRE-3 Task 1 + Task 2 — intraday entry evaluation on the exec
clock.

Task 1 (original tests below, unchanged): a *daily*-signal
strategy running in two-clock mode must load per-exec-bar
features (``load_intraday_features_window``) for the exec-
covered tickers. Plain-daily (non-two-clock) runs must NOT call
this loader at all.

Task 2 (appended below): those exec-grain features now drive an
ENTRY evaluation on every execution bar (mirroring live R1's
OR-trigger: intraday-forming leg OR prior daily-close leg), not
once/day. Falling-knife veto + 09:30 floor + once/day dedup gate
the new path; uncovered tickers and every other mode (plain-
daily, native-intraday, non-two-clock) keep the legacy once/day
signal-bar entry.

Patch targets are module-level references in ``runner`` (mirrors
``test_two_clock_runner.py``):
  - ``runner.load_ohlcv_window``            — daily signal bars
  - ``runner.intraday_coverage``            — finest grain probe
  - ``runner.load_intraday_bars_window``    — 15m execution bars
  - ``runner.load_intraday_features_window``— 15m exec features
    (the exec-bar "forming" leg's rsi_2, Task 1)
  - ``runner.flush_events``                 — no Iceberg writes
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.backtest.coverage import TickerCoverage
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.fees import IndianFeeModel, Trade
from backend.algo.strategy.ast import parse_strategy

_BASE = date(2026, 1, 1)


def _daily_bars_for(ticker, closes):
    out = []
    for i, c in enumerate(closes):
        c = Decimal(str(c))
        out.append(BarData(
            ticker=ticker, date=_BASE + timedelta(days=i),
            open=Decimal("100"), high=max(Decimal("100"), c) + 1,
            low=min(Decimal("100"), c) - 1, close=c, volume=10_000,
        ))
    return out


def _ns(d, hh, mm):
    # IST clock time → UTC instant (IST = UTC+5:30).
    ist = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
    dt = ist - timedelta(hours=5, minutes=30)
    return int(dt.timestamp() * 1_000_000_000)


def _intraday_15m_for(ticker, d, lows):
    bars = []
    for k, lo in enumerate(lows):
        hh, mm = 9 + (k * 15) // 60, (15 + k * 15) % 60
        ts = _ns(d, hh, mm)
        bars.append(BarData(
            ticker=ticker, date=d,
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal(str(lo)), close=Decimal("100"),
            volume=500, bar_open_ts_ns=ts,
        ))
    return bars


def _v5_daily_strategy():
    # reuse the trailing-enabled daily strategy shape.
    from backend.algo.backtest.tests.test_trailing_stop_integration import (
        _v5_strategy,
    )
    return _v5_strategy()


def _two_clock_fixture(ticker="FAKE.NS", num_days=25):
    daily = {ticker: _daily_bars_for(ticker, [100] * num_days)}
    cov = {ticker: TickerCoverage(
        ticker, 900, _BASE, _BASE + timedelta(days=num_days - 1),
        num_days,
    )}
    exec_bars = {ticker: []}
    for i in range(num_days):
        d = _BASE + timedelta(days=i)
        exec_bars[ticker].extend(
            _intraday_15m_for(ticker, d, [99] * num_days)
        )
    return daily, cov, exec_bars


# ── Task 2 helpers ────────────────────────────────────────────────

# One 15m slot per (hh, mm), 09:15..15:15 IST inclusive (25 bars).
_EXEC_SLOTS: list[tuple[int, int]] = []
_h, _m = 9, 15
for _ in range(25):
    _EXEC_SLOTS.append((_h, _m))
    _m += 15
    if _m >= 60:
        _m -= 60
        _h += 1


def _daily_bar(
    ticker: str,
    d: date,
    *,
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
    volume: int = 10_000,
) -> BarData:
    return BarData(
        ticker=ticker, date=d,
        open=Decimal(open_), high=Decimal(high),
        low=Decimal(low), close=Decimal(close), volume=volume,
    )


def _exec_bars_for(
    ticker: str,
    d: date,
    *,
    lows: dict[tuple[int, int], str] | None = None,
) -> list[BarData]:
    """25 15m bars for one trading day. ``lows`` overrides the
    default low=99 at specific (hh, mm) slots (e.g. to trip a
    trailing stop mid-day)."""
    lows = lows or {}
    bars = []
    for hh, mm in _EXEC_SLOTS:
        lo = lows.get((hh, mm), "99")
        bars.append(
            BarData(
                ticker=ticker, date=d,
                open=Decimal("100"), high=Decimal("101"),
                low=Decimal(lo), close=Decimal("100"),
                volume=500, bar_open_ts_ns=_ns(d, hh, mm),
            )
        )
    return bars


def _feat_panel(
    ticker: str, d: date, rsi_by_slot: dict[tuple[int, int], str],
) -> dict:
    return {
        ticker: {
            _ns(d, hh, mm): {"rsi_2": Decimal(v)}
            for (hh, mm), v in rsi_by_slot.items()
        }
    }


def _payload(event: dict) -> dict:
    """``event_row`` stores the payload pre-serialised as
    ``payload_json`` (algo.events wire shape) — decode it back
    for test assertions."""
    return json.loads(event["payload_json"])


_V5_RISK = {
    "per_trade": {
        "stop_loss_pct": 5.0,
        "max_qty": 1000,
        "phase1_ratchet_trigger_pct": 2.0,
        "phase1_ratchet_new_stop_pct": 3.0,
        "trailing_trigger_pct": 5.0,
        "trailing_atr_multiplier": 1.5,
    },
    "portfolio": {
        "max_exposure_pct": 100,
        "max_concentration_pct": 100,
    },
    "daily": {"max_loss_pct": 50, "max_open_positions": 10},
}


def _rsi2_strategy() -> dict:
    """Two-clock daily-signal strategy: BUY iff rsi_2 <= 5.
    Trailing enabled (v5 fields) so a covered ticker activates
    the two-clock execution path."""
    return {
        "id": str(uuid4()),
        "name": "rsi2 intraday entry",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 5},
        "root": {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "rsi_2"},
                "op": "<=",
                "right": {"literal": 5},
            },
            "then": {"type": "buy", "qty": {"shares": 10}},
            "else": {"type": "hold"},
        },
        "risk": _V5_RISK,
    }


def _unconditional_buy_strategy(*, trailing: bool = True) -> dict:
    risk = (
        _V5_RISK
        if trailing
        else {
            "per_trade": {"stop_loss_pct": 5.0, "max_qty": 1000},
            "portfolio": {
                "max_exposure_pct": 100, "max_concentration_pct": 100,
            },
            "daily": {"max_loss_pct": 50, "max_open_positions": 10},
        }
    )
    return {
        "id": str(uuid4()),
        "name": "unconditional buy",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 5},
        "root": {"type": "buy", "qty": {"shares": 10}},
        "risk": risk,
    }


def _run(
    *,
    strategy_dict: dict,
    daily: dict[str, list[BarData]],
    cov: dict,
    exec_bars: dict[str, list[BarData]],
    feat_panel: dict,
    universe: list[str],
    period_start: date,
    period_end: date,
    captured_events: list | None = None,
):
    strategy = parse_strategy(strategy_dict)
    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=period_start,
        period_end=period_end,
    )

    def _flush(events):  # noqa: ANN001
        if captured_events is not None:
            captured_events.extend(events)

    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage",
        return_value=cov,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_features_window",
        return_value=feat_panel,
    ), patch(
        "backend.algo.backtest.runner.flush_events",
        side_effect=_flush,
    ):
        return run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=universe,
        )


def test_two_clock_run_loads_intraday_features_for_exec_covered():
    ticker = "FAKE.NS"
    daily, cov, exec_bars = _two_clock_fixture(ticker)
    strategy = parse_strategy(_v5_daily_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage",
        return_value=cov,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_features_window",
        return_value={},
    ) as mock_features, patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=[ticker],
        )
    assert mock_features.called, (
        "two-clock daily-signal run must load intraday features "
        "at the exec grain"
    )
    _, kwargs = mock_features.call_args
    assert kwargs["interval_sec"] == 900
    assert set(kwargs["tickers"]) == {ticker}
    assert kwargs["period_start"] == req.period_start
    assert kwargs["period_end"] == req.period_end


def test_two_clock_features_skip_daily_fallback_ticker():
    # A ticker with NO 15m coverage is not in ``exec_covered`` and
    # must not be passed to the features loader.
    covered = "COVERED.NS"
    uncov = "UNCOVERED.NS"
    daily_c, cov_c, exec_bars_c = _two_clock_fixture(covered)
    daily = {
        **daily_c,
        uncov: _daily_bars_for(uncov, [100] * 25),
    }
    strategy = parse_strategy(_v5_daily_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage",
        return_value=cov_c,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars_c,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_features_window",
        return_value={},
    ) as mock_features, patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=[covered, uncov],
        )
    assert mock_features.called
    _, kwargs = mock_features.call_args
    assert set(kwargs["tickers"]) == {covered}


def test_plain_daily_run_never_loads_intraday_features():
    # No two-clock trigger (trailing disabled / no coverage probed
    # here since ``intraday_coverage`` isn't even reached) — a
    # plain daily run must not touch the features loader at all.
    ticker = "FAKE.NS"
    daily = {ticker: _daily_bars_for(ticker, [100] * 25)}
    strategy_dict = _v5_daily_strategy()
    # Disable trailing so the two-clock probe never fires — this
    # collapses to the pure-daily path exactly as today.
    strategy_dict["risk"]["per_trade"]["trailing_trigger_pct"] = None
    strategy_dict["risk"]["per_trade"]["trailing_atr_multiplier"] = None
    strategy = parse_strategy(strategy_dict)
    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage",
    ) as mock_cov, patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
    ) as mock_bars, patch(
        "backend.algo.backtest.runner.load_intraday_features_window",
    ) as mock_features, patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=[ticker],
        )
    assert not mock_cov.called, "plain-daily must skip the probe"
    assert not mock_bars.called
    assert not mock_features.called, (
        "plain-daily (non-two-clock) run must not load intraday "
        "features"
    )


# ── Task 2 §6 test matrix ────────────────────────────────────────
# (1) Intraday-only dip — daily close does NOT confirm ────────────


def test_intraday_only_dip_enters_mid_day():
    ticker = "DIP.NS"
    d = _BASE + timedelta(days=8)
    daily = {ticker: _daily_bars_for(ticker, [100] * 9)}
    cov = {
        ticker: TickerCoverage(ticker, 900, _BASE, d, 9),
    }
    exec_bars = {ticker: _exec_bars_for(ticker, d)}
    # rsi_2 neutral (50) all day except an oversold print at 11:00.
    rsi_by_slot = {slot: "50" for slot in _EXEC_SLOTS}
    rsi_by_slot[(11, 0)] = "3"
    feat_panel = _feat_panel(ticker, d, rsi_by_slot)

    summary = _run(
        strategy_dict=_rsi2_strategy(),
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ticker],
        period_start=d, period_end=d,
    )
    assert len(summary.trade_list) == 1, summary.trade_list
    trade = summary.trade_list[0]
    # Decision at 11:00 -> fills at the NEXT exec bar's open (11:15).
    assert trade.opened_at_ts_ns == _ns(d, 11, 15), (
        f"expected fill at 11:15 (bar after the 11:00 dip), "
        f"got {trade.opened_at_ts_ns}"
    )


# ── (2) Daily-close oversold, intraday bounced -> still enters ──


def test_daily_close_oversold_but_bounced_still_enters():
    ticker = "BOUNCE.NS"
    # Wilder RSI(2): 20 flat days, then +1%, then a -9.9% crash ->
    # rsi_2 = 4.76 on the crash day (day21); ret_3d over
    # days[18..21] = 91/100-1 = -9% (stays inside the -10% knife
    # floor). day22 is the entry day; a fresh close/open keeps it
    # a flat +9.9% gap-up (no gap veto either).
    closes = [100] * 20 + [101, 91, 100]
    d = _BASE + timedelta(days=22)
    daily = {ticker: _daily_bars_for(ticker, closes)}
    cov = {ticker: TickerCoverage(ticker, 900, _BASE, d, 23)}
    exec_bars = {ticker: _exec_bars_for(ticker, d)}
    # Forming leg bounced (80, not oversold) for the WHOLE day —
    # only the OR-trigger's closed-bar leg can fire.
    rsi_by_slot = {slot: "80" for slot in _EXEC_SLOTS}
    feat_panel = _feat_panel(ticker, d, rsi_by_slot)

    summary = _run(
        strategy_dict=_rsi2_strategy(),
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ticker],
        period_start=d, period_end=d,
    )
    assert len(summary.trade_list) == 1, summary.trade_list
    trade = summary.trade_list[0]
    # Closed leg is available from the first eligible (09:30) bar
    # -> fills at the next exec bar's open (09:45).
    assert trade.opened_at_ts_ns == _ns(d, 9, 45), (
        f"expected the closed-bar OR-trigger leg to fill at "
        f"09:45, got {trade.opened_at_ts_ns}"
    )


# ── (3) Falling-knife veto blocks a would-be BUY ─────────────────


def test_falling_knife_veto_blocks_entry():
    ret3d_ticker = "KNIFE3D.NS"
    gap_ticker = "KNIFEGAP.NS"
    d = _BASE + timedelta(days=23)

    # ret_3d leg: days[19..22] = 100,100,100,85 -> ret_3d = -15%.
    # Entry-day open pinned to 85 too so the gap leg stays neutral
    # (isolates the ret_3d veto).
    ret3d_bars = _daily_bars_for(ret3d_ticker, [100] * 20 + [100, 100, 85])
    ret3d_bars.append(
        _daily_bar(ret3d_ticker, d, open_="85", close="86")
    )
    # gap leg: flat closes (ret_3d = 0%) but entry-day open gaps
    # -6% below yesterday's close of 100 (isolates the gap veto).
    gap_bars = _daily_bars_for(gap_ticker, [100] * 23)
    gap_bars.append(_daily_bar(gap_ticker, d, open_="94", close="100"))

    daily = {ret3d_ticker: ret3d_bars, gap_ticker: gap_bars}
    cov = {
        ret3d_ticker: TickerCoverage(ret3d_ticker, 900, _BASE, d, 24),
        gap_ticker: TickerCoverage(gap_ticker, 900, _BASE, d, 24),
    }
    exec_bars = {
        ret3d_ticker: _exec_bars_for(ret3d_ticker, d),
        gap_ticker: _exec_bars_for(gap_ticker, d),
    }
    # Both would fire on the forming leg (rsi_2=3 all day) if the
    # veto didn't block them first.
    rsi_by_slot = {slot: "3" for slot in _EXEC_SLOTS}
    feat_panel = {
        **_feat_panel(ret3d_ticker, d, rsi_by_slot),
        **_feat_panel(gap_ticker, d, rsi_by_slot),
    }
    captured: list = []

    summary = _run(
        strategy_dict=_rsi2_strategy(),
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ret3d_ticker, gap_ticker],
        period_start=d, period_end=d, captured_events=captured,
    )
    assert summary.trade_list == [], summary.trade_list
    knife_events = [
        e for e in captured
        if e.get("type") == "signal_rejected"
        and _payload(e).get("reason") == "falling_knife_veto"
    ]
    vetoed_tickers = {_payload(e)["ticker"] for e in knife_events}
    assert vetoed_tickers == {ret3d_ticker, gap_ticker}, vetoed_tickers


# ── (4) 09:30 floor — no entry on the 09:15 opening bar ──────────


def test_no_entry_on_0915_opening_bar():
    ticker = "FLOOR.NS"
    d = _BASE + timedelta(days=5)
    daily = {ticker: _daily_bars_for(ticker, [100] * 6)}
    cov = {ticker: TickerCoverage(ticker, 900, _BASE, d, 6)}
    exec_bars = {ticker: _exec_bars_for(ticker, d)}
    # Oversold from the very first (09:15) bar onward, all day.
    rsi_by_slot = {slot: "3" for slot in _EXEC_SLOTS}
    feat_panel = _feat_panel(ticker, d, rsi_by_slot)

    summary = _run(
        strategy_dict=_rsi2_strategy(),
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ticker],
        period_start=d, period_end=d,
    )
    assert len(summary.trade_list) == 1, summary.trade_list
    trade = summary.trade_list[0]
    # If 09:15 were allowed, the fill would land at 09:30's open.
    assert trade.opened_at_ts_ns != _ns(d, 9, 30)
    # The floor pushes the decision to 09:30 -> fill at 09:45.
    assert trade.opened_at_ts_ns == _ns(d, 9, 45), (
        f"expected the 09:30 floor to defer the fill to 09:45, "
        f"got {trade.opened_at_ts_ns}"
    )


# ── (5) Once-per-day dedup, even across a same-day exit ──────────


def test_once_per_day_dedup_across_intraday_exit():
    ticker = "DEDUP.NS"
    d = _BASE + timedelta(days=20)
    # 21 flat days (ATR-14 well-formed: constant 2-pt true range).
    daily = {ticker: _daily_bars_for(ticker, [100] * 21)}
    cov = {ticker: TickerCoverage(ticker, 900, _BASE, d, 21)}
    # Deep low at 11:00 trips the 5% hard stop (entry ~100 -> 95)
    # well after the entry fills at 09:45.
    exec_bars = {
        ticker: _exec_bars_for(ticker, d, lows={(11, 0): "80"}),
    }
    # Oversold ALL DAY -> without the dedup guard this would
    # re-enter immediately after the stop-out flattens it.
    rsi_by_slot = {slot: "3" for slot in _EXEC_SLOTS}
    feat_panel = _feat_panel(ticker, d, rsi_by_slot)

    summary = _run(
        strategy_dict=_rsi2_strategy(),
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ticker],
        period_start=d, period_end=d,
    )
    assert len(summary.trade_list) == 1, (
        f"expected exactly one entry/exit pair for the whole day "
        f"despite rsi_2<=5 persisting after the stop-out, got "
        f"{summary.trade_list}"
    )
    trade = summary.trade_list[0]
    assert trade.exit_reason in {
        "phase1_stop", "phase1_ratchet", "trail_stop",
    }, trade.exit_reason


# ── (6) Fee product = DELIVERY on an intraday CNC entry ──────────


def test_intraday_entry_bills_delivery_fees_for_cnc():
    ticker = "FEE.NS"
    d = _BASE + timedelta(days=8)
    daily = {ticker: _daily_bars_for(ticker, [100] * 9)}
    cov = {ticker: TickerCoverage(ticker, 900, _BASE, d, 9)}
    exec_bars = {ticker: _exec_bars_for(ticker, d)}
    rsi_by_slot = {slot: "50" for slot in _EXEC_SLOTS}
    rsi_by_slot[(11, 0)] = "3"
    feat_panel = _feat_panel(ticker, d, rsi_by_slot)
    captured: list = []

    strategy_dict = _rsi2_strategy()
    assert strategy_dict.get("product", "CNC") == "CNC"  # default

    summary = _run(
        strategy_dict=strategy_dict,
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ticker],
        period_start=d, period_end=d, captured_events=captured,
    )
    assert len(summary.trade_list) == 1, summary.trade_list

    buy_fills = [
        e for e in captured
        if e.get("type") == "order_filled"
        and _payload(e).get("side") == "BUY"
    ]
    assert len(buy_fills) == 1, buy_fills
    payload = _payload(buy_fills[0])
    fill_price = Decimal(payload["fill_price"])
    booked_fees = Decimal(payload["fees_inr"])

    fees = IndianFeeModel(as_of=d)
    delivery_fees = fees.compute(
        Trade(
            symbol=ticker, exchange="NSE", side="BUY",
            product="DELIVERY", qty=payload["qty"], price=fill_price,
        )
    ).total_inr
    intraday_fees = fees.compute(
        Trade(
            symbol=ticker, exchange="NSE", side="BUY",
            product="INTRADAY", qty=payload["qty"], price=fill_price,
        )
    ).total_inr

    assert booked_fees == delivery_fees, (
        f"CNC intraday-triggered entry must bill DELIVERY fees "
        f"({delivery_fees}), got {booked_fees}"
    )
    assert delivery_fees != intraday_fees, (
        "fixture is vacuous — DELIVERY and INTRADAY fees must "
        "differ for this test to prove anything"
    )


# ── (7) Uncovered ticker -> daily-fallback once/day entry ────────


def test_uncovered_ticker_keeps_daily_fallback_entry():
    covered = "COV.NS"
    uncovered = "UNCOV.NS"
    d0 = _BASE
    daily = {
        covered: _daily_bars_for(covered, [100] * 5),
        uncovered: _daily_bars_for(uncovered, [100] * 5),
    }
    cov = {covered: TickerCoverage(covered, 900, d0, d0, 5)}
    exec_bars = {
        covered: [
            *_exec_bars_for(covered, d0 + timedelta(days=2)),
            *_exec_bars_for(covered, d0 + timedelta(days=3)),
            *_exec_bars_for(covered, d0 + timedelta(days=4)),
        ],
    }
    strategy_dict = _unconditional_buy_strategy(trailing=True)

    summary = _run(
        strategy_dict=strategy_dict,
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel={}, universe=[covered, uncovered],
        period_start=d0 + timedelta(days=2),
        period_end=d0 + timedelta(days=4),
    )
    uncov_trades = [
        t for t in summary.trade_list if t.ticker == uncovered
    ]
    assert len(uncov_trades) == 1, uncov_trades
    # Daily-clock fill (via the legacy signal-bar path) carries no
    # intraday timestamp.
    assert uncov_trades[0].opened_at_ts_ns is None


# ── (8) Parity — a plain-daily (non-two-clock) run is unchanged ─


def test_plain_daily_run_entries_unchanged():
    ticker = "PLAIN.NS"
    daily = {ticker: _daily_bars_for(ticker, [100] * 5)}
    strategy_dict = _unconditional_buy_strategy(trailing=False)

    strategy = parse_strategy(strategy_dict)
    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE + timedelta(days=2),
        period_end=_BASE + timedelta(days=4),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch("backend.algo.backtest.runner.flush_events"):
        summary = run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=[ticker],
        )
    assert len(summary.trade_list) == 1, summary.trade_list
    # No execution clock at all -> the entry fill carries no
    # intraday timestamp, exactly as before this feature existed.
    assert summary.trade_list[0].opened_at_ts_ns is None
    assert summary.execution_interval_sec == 86400


# ── Fix-loop round 1 (HIGH) — AST-level exit on a HELD covered
# ticker. Pre-fix, the legacy ``is_signal_bar`` block's skip for
# ``exec_covered`` tickers dropped this entirely: a HELD covered
# ticker's ``sell``/``exit`` leg was never evaluated intraday, so
# it could only leave via a stop/trailing/time/regime exit —
# diverging from live (which acts on AST sells every bar-close
# from 09:30) and poisoning the holding-period/outcome labels this
# feature exists to produce. Manually verified this test FAILS
# (no "signal" exit; the position rides to period-end instead) if
# the held-ticker branch in ``_evaluate_intraday_entry`` is
# reverted to an unconditional ``continue``.


def test_ast_sell_exits_intraday_for_held_covered_ticker():
    ticker = "EXIT.NS"
    d = _BASE + timedelta(days=8)
    # Flat daily closes -> the closed-bar OR-trigger leg never
    # fires (rsi_2 stays 100 all warmup) — only the forming leg
    # (mocked per-slot below) can enter or exit this ticker.
    daily = {ticker: _daily_bars_for(ticker, [100] * 9)}
    cov = {ticker: TickerCoverage(ticker, 900, _BASE, d, 9)}
    exec_bars = {ticker: _exec_bars_for(ticker, d)}
    # Entry trigger at 09:30 (rsi_2<=5); neutral in between;
    # explicit AST exit trigger at 11:00 (rsi_2>=70).
    rsi_by_slot = {slot: "50" for slot in _EXEC_SLOTS}
    rsi_by_slot[(9, 30)] = "3"
    rsi_by_slot[(11, 0)] = "75"
    feat_panel = _feat_panel(ticker, d, rsi_by_slot)

    strategy_dict = _rsi2_strategy()
    # Extend the entry-only root with an explicit exit leg,
    # mirroring live's v5 AST shape ("explicit exit leg
    # rsi_2 >= 70"): BUY on oversold, else EXIT on overbought,
    # else hold.
    strategy_dict["root"]["else"] = {
        "type": "if",
        "cond": {
            "type": "compare",
            "left": {"feature": "rsi_2"},
            "op": ">=",
            "right": {"literal": 70},
        },
        "then": {"type": "exit", "scope": "this_symbol"},
        "else": {"type": "hold"},
    }

    captured: list = []
    summary = _run(
        strategy_dict=strategy_dict,
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ticker],
        period_start=d, period_end=d, captured_events=captured,
    )
    assert len(summary.trade_list) == 1, summary.trade_list
    trade = summary.trade_list[0]
    assert trade.opened_at_ts_ns == _ns(d, 9, 45), trade
    # AST SIGNAL exit (not a stop/trailing/time/regime exit) must
    # fire intraday: decision at 11:00 -> fills at 11:15's open.
    assert trade.exit_reason == "signal", (
        f"expected an AST-level signal exit, got "
        f"{trade.exit_reason!r} (a non-'signal' reason means the "
        f"AST sell was dropped and the position rode to a "
        f"different exit path instead)"
    )
    assert trade.closed_at_ts_ns == _ns(d, 11, 15), (
        f"expected the 11:00 AST exit decision to fill at 11:15, "
        f"got {trade.closed_at_ts_ns}"
    )

    # Fix-loop round 2 (MUST-FIX) — the AST-exit SELL must bill
    # DELIVERY fees for this CNC strategy, exactly like the
    # entry-side regression (test 6). ``_action_to_intent``'s
    # ``sell``/``exit`` branches used to drop ``product``
    # entirely, so ``SimBroker`` inferred INTRADAY from the real
    # exec-bar ``ts_ns`` and mis-billed cheap intraday STT/
    # brokerage on a CNC exit.
    sell_fills = [
        e for e in captured
        if e.get("type") == "order_filled"
        and _payload(e).get("side") == "SELL"
    ]
    assert len(sell_fills) == 1, sell_fills
    sell_payload = _payload(sell_fills[0])
    exit_fill_price = Decimal(sell_payload["fill_price"])
    exit_booked_fees = Decimal(sell_payload["fees_inr"])

    fees = IndianFeeModel(as_of=d)
    exit_delivery_fees = fees.compute(
        Trade(
            symbol=ticker, exchange="NSE", side="SELL",
            product="DELIVERY", qty=sell_payload["qty"],
            price=exit_fill_price,
        )
    ).total_inr
    exit_intraday_fees = fees.compute(
        Trade(
            symbol=ticker, exchange="NSE", side="SELL",
            product="INTRADAY", qty=sell_payload["qty"],
            price=exit_fill_price,
        )
    ).total_inr

    assert exit_booked_fees == exit_delivery_fees, (
        f"CNC intraday-triggered AST exit must bill DELIVERY fees "
        f"({exit_delivery_fees}), got {exit_booked_fees} — "
        f"'sell'/'exit' branches of _action_to_intent must forward "
        f"``product`` into the OrderIntent"
    )
    assert exit_delivery_fees != exit_intraday_fees, (
        "fixture is vacuous — DELIVERY and INTRADAY fees must "
        "differ for this test to prove anything"
    )


def test_held_ticker_set_target_weight_never_trims_via_exec_clock():
    # Regression guard for the fee-product/held-ticker fix: an
    # unconditional ``set_target_weight`` root must still NEVER
    # trim an open position once two-clock exec-bar AST
    # evaluation reaches a HELD ticker every 15m (mirrors the
    # existing daily-clock contract — reductions only ever come
    # from an explicit exit/stop_loss/time_stop/regime_exit).
    ticker = "REBAL.NS"
    d = _BASE + timedelta(days=8)
    daily = {ticker: _daily_bars_for(ticker, [100] * 9)}
    cov = {ticker: TickerCoverage(ticker, 900, _BASE, d, 9)}
    exec_bars = {ticker: _exec_bars_for(ticker, d)}
    # set_target_weight doesn't reference rsi_2, but the forming
    # leg still needs a non-None feature row to evaluate at all.
    rsi_by_slot = {slot: "50" for slot in _EXEC_SLOTS}
    feat_panel = _feat_panel(ticker, d, rsi_by_slot)

    strategy_dict = _rsi2_strategy()
    strategy_dict["root"] = {
        "type": "set_target_weight", "weight": 0.01,
    }
    captured: list = []

    summary = _run(
        strategy_dict=strategy_dict,
        daily=daily, cov=cov, exec_bars=exec_bars,
        feat_panel=feat_panel, universe=[ticker],
        period_start=d, period_end=d, captured_events=captured,
    )
    assert len(summary.trade_list) == 1, summary.trade_list
    # Position rides to the period-end force-close, not a "signal"
    # SELL fired mid-day by the held-ticker AST-exit path.
    assert summary.trade_list[0].exit_reason == "period_end_mtm"

    sell_fills = [
        e for e in captured
        if e.get("type") == "order_filled"
        and _payload(e).get("side") == "SELL"
    ]
    assert sell_fills == [], (
        f"set_target_weight must never trim a held position via "
        f"the exec clock, got SELL fill(s): {sell_fills}"
    )
