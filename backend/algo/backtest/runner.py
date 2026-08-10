"""Backtest orchestrator. Walks daily bars over a closed period,
evaluates the strategy AST per (ticker, bar), routes action
results to SimBroker, accumulates positions, and emits an event
log + summary.

Per CLAUDE.md §4.1: single bulk OHLCV read, single Iceberg
commit at the end (not per-event), no per-ticker hot loops.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.backtest.coverage import intraday_coverage
from backend.algo.backtest.data_source import (
    load_intraday_bars_window,
    load_ohlcv_window,
)
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.execution_simulator import ExecutionSimulator
from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.indicators import (
    DEFAULT_WARMUP_BARS,
    compute_indicators_for_universe,
    compute_market_distance_from_sma200,
    compute_market_regime,
    compute_market_trend_strength,
)
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.sim_broker import (
    NoBarAvailableError,
    SimBroker,
)
from backend.algo.backtest.cooldown_monitor import in_cooldown
from backend.algo.backtest.regime_exit_monitor import (
    check_regime_exit_triggers,
)
from backend.algo.backtest.stop_loss_monitor import (
    check_stop_loss_triggers,
)
from backend.algo.backtest.time_stop_monitor import (
    check_time_stop_triggers,
)
from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
from backend.algo.features.primitives import wilder_atr as _wilder_atr
from backend.algo.backtest.types import (
    BacktestRequest,
    BacktestSummary,
    EquityPoint,
    OrderIntent,
    TradeRow,
)

# REGIME-2a — pre-computed nightly factor library overlay.
from backend.algo.factors.repo import get_factors_window
from backend.algo.features import (
    DEFAULT_INTRADAY_WARMUP_DAYS,
    FeaturePanelMissingError,
    load_intraday_features_window,
)
from backend.algo.features.per_bar import (
    assemble_per_bar_features,
    lookup_daily_overlay,
)
from backend.algo.paper.risk_engine import RiskEngine
from backend.algo.paper.types import AccountState, Signal
from backend.algo.runtime.intraday_window import (
    is_entry_allowed,
    ist_time_from_ns,
)

# REGIME-4 — 3-stage sizer (vol-target / Kelly → caps → DD throttle).
from backend.algo.sizing.composer import SizingContext, compose_qty
from backend.algo.strategy.ast import Strategy

# REGIME-7 — pre-load 60d ADTV per ticker for slippage model.
from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)


def _trade_row(p, fill_price: Decimal) -> TradeRow:  # noqa: ANN001
    """Project a closed Position into a TradeRow for the UI."""
    holding_days = (p.closed_at - p.opened_at).days if p.closed_at else 0
    return_pct = (
        ((fill_price - p.avg_price) / p.avg_price) * Decimal("100")
        if p.avg_price > 0
        else Decimal("0")
    )
    return TradeRow(
        ticker=p.ticker,
        qty=p.qty,
        avg_price=p.avg_price,
        fill_price=fill_price,
        opened_at=p.opened_at,
        closed_at=p.closed_at,
        holding_days=holding_days,
        realised_pnl_inr=p.realised_pnl_inr,
        return_pct=return_pct,
        exit_reason=getattr(p, "exit_reason", "signal"),
        opened_at_ts_ns=getattr(p, "opened_at_ts_ns", None),
        closed_at_ts_ns=getattr(p, "closed_at_ts_ns", None),
    )


def run_backtest(
    *,
    strategy: Strategy,
    request: BacktestRequest,
    user_id: UUID,
    universe: list[str],
) -> BacktestSummary:
    """Run a backtest end-to-end and return the summary.

    Caller responsibilities:
    - Persist the Strategy AST and generate ``request``.
    - Resolve ``universe`` from ``strategy.universe`` (Slice 7
      uses the user's watchlist union holdings; this function
      treats it as opaque input).
    - Persist the returned ``BacktestSummary`` to ``algo.runs``
      and the events emitted by ``flush_events`` to
      ``algo.events`` — both happen automatically at the end of
      this call.
    """
    started_at = datetime.now(timezone.utc)
    run_id = uuid4()
    session_id = run_id
    events: list[dict[str, Any]] = []

    events.append(
        event_row(
            session_id=session_id,
            user_id=user_id,
            strategy_id=strategy.id,
            mode="backtest",
            type_="backtest_run_started",
            payload={
                "period_start": request.period_start.isoformat(),
                "period_end": request.period_end.isoformat(),
                "universe_size": len(universe),
                "initial_capital_inr": str(request.initial_capital_inr),
            },
        )
    )

    # ASETPLTFRM-400 slice 3 — dispatch on cadence.
    # 86400 → daily (original path, unchanged).
    # 60 / 300 / 900 → intraday loader; the runner walks
    # ``(bar_date, bar_open_ts_ns)`` tuples instead of dates.
    is_intraday = request.interval_sec != 86400

    # §8 — block 1m/5m backtests when no preserved history exists.
    # Only 15m (900s) history is stored today; a strategy requesting
    # 60s or 300s cannot be backtested faithfully. Fail loudly so
    # users switch to paper rather than silently receiving an empty
    # result. The guard calls intraday_coverage (already imported at
    # module top) and is skipped for daily (86400) and 15m (900)
    # cadences so existing backtests are unaffected.
    if request.interval_sec in (60, 300):
        _cov = intraday_coverage(
            tickers=universe,
            period_start=request.period_start,
            period_end=request.period_end,
        )
        if not any(
            c.finest_interval_sec is not None
            and c.finest_interval_sec <= request.interval_sec
            for c in _cov.values()
        ):
            _label = "1m" if request.interval_sec == 60 else "5m"
            raise ValueError(
                f"No {_label} history for these tickers; faithful "
                f"backtest unavailable. Run in paper to evaluate "
                f"this cadence."
            )

    if is_intraday:
        bars = load_intraday_bars_window(
            tickers=universe,
            interval_sec=request.interval_sec,
            period_start=request.period_start,
            period_end=request.period_end,
            warmup_days=DEFAULT_INTRADAY_WARMUP_DAYS,
        )
        # ASETPLTFRM-402 / FE-4 — intraday features now sourced
        # from ``stocks.intraday_features`` via the partition-
        # chunk Redis loader. Slice-4b's in-memory
        # ``compute_indicators_for_universe_intraday`` is
        # deleted; on cache miss the loader scans Iceberg, and
        # on Iceberg miss it triggers an on-demand backfill
        # (spec §7.3 — no in-memory fallback).
        try:
            intraday_indicators = load_intraday_features_window(
                tickers=list(bars.keys()),
                interval_sec=request.interval_sec,
                period_start=request.period_start,
                period_end=request.period_end,
            )
        except FeaturePanelMissingError as exc:
            _logger.error(
                "[backtest-runner] feature panel missing for "
                "intraday run (interval_sec=%d, %s..%s): %s",
                request.interval_sec,
                request.period_start,
                request.period_end,
                exc,
                exc_info=True,
            )
            raise
        # Date-keyed indicator dict left empty for intraday — the
        # daily-path lookup in the inner loop returns {} and the
        # intraday lookup below supplies the features.
        indicators: dict[str, dict[date, dict[str, Decimal]]] = {}
        # FE-15b — cross-cadence DAILY overlay for intraday
        # strategies. Loads ``stocks.intraday_features`` at
        # ``interval_sec=86400`` over the same window. The shared
        # per-bar helper injects these under ``{name}_1d`` keys
        # so an AST can reference both 15m ``rsi_14`` and daily
        # ``rsi_14_1d`` simultaneously. Absent panel → empty
        # dict (strategies without _1d refs are unaffected).
        try:
            daily_overlay_panel = load_intraday_features_window(
                tickers=list(bars.keys()),
                interval_sec=86400,
                period_start=request.period_start,
                period_end=request.period_end,
                enable_on_demand_backfill=False,
            )
        except FeaturePanelMissingError as exc:
            _logger.warning(
                "[backtest-runner] daily overlay panel missing "
                "(interval_sec=86400, %s..%s) — strategies that "
                "reference _1d keys will see KeyError. Run the "
                "daily_features_daily_compute backfill to populate: %s",
                request.period_start,
                request.period_end,
                exc,
            )
            daily_overlay_panel = {}
    else:
        # Load with warmup history so SMA200 etc. are well-formed
        # at period_start. Indicators computed once over the FULL
        # series; the bar walk below skips warmup-only dates.
        bars = load_ohlcv_window(
            tickers=universe,
            period_start=request.period_start,
            period_end=request.period_end,
            warmup_days=DEFAULT_WARMUP_BARS,
        )
        indicators = compute_indicators_for_universe(bars)
        intraday_indicators: dict[str, dict[int, dict[str, Decimal | str]]] = (
            {}
        )
        # FE-15b — daily strategies see daily features unsuffixed
        # (primary cadence). No _1d overlay needed.
        daily_overlay_panel: dict[
            str, dict[int, dict[str, Decimal | str]]
        ] = {}
    # Top-level regime feature, injected into every (ticker, bar)
    # feature dict below so strategies can gate entries on
    # `{"feature": "nifty_above_sma200"}`. Empty dict if ^NSEI
    # absent → callers fall back to Decimal("0") (regime off).
    market_regime = compute_market_regime(
        period_start=request.period_start,
        period_end=request.period_end,
    )
    market_trend = compute_market_trend_strength(
        period_start=request.period_start,
        period_end=request.period_end,
    )
    # Continuous-band counterpart to ``market_regime`` — percent
    # distance from SMA200 instead of the binary 1/0 flag. Empty
    # dict if ^NSEI absent → callers fall back to Decimal("0").
    market_dist_sma200 = compute_market_distance_from_sma200(
        period_start=request.period_start,
        period_end=request.period_end,
    )
    # REGIME-2a — pre-load cached daily factor rows for the
    # period. Disjoint from indicator keys by design; overlaid
    # AFTER the indicator dict in the per-bar features assembly
    # below. Empty dict if backfill hasn't run yet — strategies
    # that don't reference factor keys are unaffected.
    factor_rows = get_factors_window(
        tickers=universe,
        start=request.period_start,
        end=request.period_end,
    )
    factors_by_key: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in factor_rows:
        factors_by_key[(r.ticker, r.bar_date)] = {
            k: Decimal(str(v)) for k, v in r.values.items() if v is not None
        }
    # REGIME-1 — pre-load regime_label + stress_prob for the
    # period so per-bar features can resolve regime-aware
    # templates (`{"feature": "regime_label"}`,
    # `{"feature": "stress_prob"}`). Empty dict if
    # regime_history is empty for this window — strategies that
    # don't reference regime keys are unaffected.
    regime_by_date: dict[date, dict[str, Any]] = {}
    try:
        from backend.algo.regime.repo import get_regime_history

        rh_rows = get_regime_history(
            request.period_start,
            request.period_end,
        )
        for rh in rh_rows:
            entry: dict[str, Any] = {"regime_label": rh.regime_label}
            if rh.stress_prob is not None:
                entry["stress_prob"] = Decimal(str(rh.stress_prob))
            regime_by_date[rh.bar_date] = entry
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "regime_history lookup failed (%s) — regime-aware "
            "templates will silently no-op for this run",
            exc,
        )
    # REGIME-7 — pre-compute 60d ADTV per ticker so SimBroker can
    # apply ``max(5, 50 * order_value / ADTV) bps`` slippage.
    # Empty universe → empty lookup → SimBroker falls back to the
    # 5bps minimum on every leg.
    adtv_lookup: dict[str, Decimal] = {}
    if universe:
        from datetime import timedelta as _td

        adtv_start = request.period_start - _td(days=90)
        try:
            adtv_rows = query_iceberg_table(
                "stocks.ohlcv",
                "SELECT ticker, AVG(close * volume) AS adtv "
                "FROM ohlcv "
                "WHERE ticker IN ({}) "
                "  AND date BETWEEN ? AND ? "
                "GROUP BY ticker".format(",".join(["?"] * len(universe))),
                [*universe, adtv_start, request.period_start],
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "ADTV lookup failed (%s) — falling back to 5bps "
                "minimum slippage on every leg",
                exc,
            )
            adtv_rows = []
        for r in adtv_rows:
            adtv_lookup[r["ticker"]] = Decimal(str(r["adtv"] or 0))

    sim = SimBroker(
        bars=bars,
        fee_as_of=request.period_start,
        adtv_lookup=adtv_lookup,
    )
    evaluator = Evaluator()
    pt = PositionTracker()
    risk = RiskEngine()
    risk_payload = strategy.risk.model_dump()

    fee_rates_version = ""
    total_fees = Decimal("0")
    equity_points: list[EquityPoint] = []
    peak_equity = request.initial_capital_inr
    max_drawdown_pct = Decimal("0")
    rejected_count = 0
    scaled_count = 0
    # Per-feature KeyError counter — surfaces the most-frequently
    # missing strategy feature so users can spot wrong/typo'd
    # references vs genuinely-not-yet-computed factors. Logged in
    # the run summary line, capped at top 5.
    _key_err_counts: dict[str, int] = {}
    # ASETPLTFRM-434 — cumulative count of entries skipped by the
    # repeat-offender cooldown gate. Surfaced in the run-summary
    # log line so operators can see the gate's impact.
    cooldown_skip_count = 0
    # ASETPLTFRM-435 v4 — cumulative count of positions force-
    # closed by the mid-trade regime exit monitor.
    regime_exit_count = 0
    # Day-bucket realised P&L so RiskEngine.daily_loss_cap fires
    # on intra-day drawdown, not cumulative-from-start. Reset at
    # the top of each bar_date.
    day_start_realised = Decimal("0")
    last_bar_date = None
    # Per-ticker most-recent close at-or-before the current
    # bar_date. Used to mark open positions to market for the
    # unrealised-P&L contribution to the daily equity curve.
    # Updated as we walk the inner loop; persists across days
    # so a ticker that doesn't trade today (holiday / new
    # listing gap) keeps its prior close as the mark.
    last_close: dict[str, Decimal] = {}

    # Walk bars chronologically. Timeline is a sorted list of
    # ``(bar_date, bar_open_ts_ns | None)`` tuples — daily runs
    # carry None for the ns slot, intraday runs carry the real
    # ns-since-epoch UTC stamp. Bars at the same instant across
    # tickers step in lockstep (slice-3 semantics for cross-
    # sectional MIS strategies).
    if is_intraday:
        timeline: list[tuple[date, int | None]] = sorted(
            {
                (b.date, b.bar_open_ts_ns)
                for blist in bars.values()
                for b in blist
                if b.bar_open_ts_ns is not None
            },
            key=lambda x: (x[1] or 0, x[0]),
        )
        # Per-ticker ts_ns → BarData lookup for O(1) bar fetch.
        bars_by_ts: dict[str, dict[int, Any]] = {
            t: {
                b.bar_open_ts_ns: b
                for b in blist
                if b.bar_open_ts_ns is not None
            }
            for t, blist in bars.items()
        }
    else:
        timeline = sorted(
            {(b.date, None) for blist in bars.values() for b in blist}
        )
        bars_by_ts = {}

    # MIS daily square-off support — pick ONE bar per trading day
    # to fire the square-off on. Honour ``strategy.square_off_time``
    # by selecting the FIRST bar whose IST open ≥ square_off_time;
    # fall back to the last bar of the day when no candidate exists
    # (e.g. square_off configured after market close).
    is_mis = strategy.product == "MIS"
    day_end_keys: set[tuple[date, int | None]] = set()
    if is_intraday and is_mis:
        from backend.algo.runtime.intraday_window import parse_ist_time

        square_off_ist = parse_ist_time(
            strategy.square_off_time
        ) or parse_ist_time("15:14 IST")
        # Group bar ts_ns by trading day.
        by_day: dict[date, list[tuple[int, "time"]]] = {}
        from datetime import time  # noqa: E402  (local import)

        for bd, ns in timeline:
            if ns is None:
                continue
            bar_ist = ist_time_from_ns(ns)
            if bar_ist is None:
                continue
            by_day.setdefault(bd, []).append((ns, bar_ist))
        for bd, entries in by_day.items():
            at_or_after = [(ns, t) for ns, t in entries if t >= square_off_ist]
            if at_or_after:
                # Earliest bar opening at-or-after square_off — that's
                # the first chance the simulation has to model the
                # forced exit.
                ns_chosen = min(at_or_after, key=lambda x: x[1])[0]
            else:
                # No bar opens at-or-after the configured square-off
                # (e.g. square_off=16:00 IST on NSE which closes at
                # 15:30). Safety net: use the last bar of the day.
                ns_chosen = max(entries, key=lambda x: x[0])[0]
            day_end_keys.add((bd, ns_chosen))

    # v5 trailing stop — disabled when both ATR-trail fields are None.
    _trailing_enabled = (
        strategy.risk.per_trade.trailing_trigger_pct is not None
        and strategy.risk.per_trade.trailing_atr_multiplier is not None
    )
    # ticker → TrailingStopManager; created on confirmed BUY fill.
    _trailing_managers: dict[str, TrailingStopManager] = {}

    # ── Two-clock (ASETPLTFRM-4xx) ──────────────────────────────
    # A *daily*-signal strategy with trailing enabled runs its
    # exit checks on the finest available intraday grain (15m
    # today) while signals still fire once per trading day. When
    # there's no intraday coverage (or trailing is off) the
    # execution clock collapses to the signal clock and behaviour
    # is byte-identical to the pure-daily path.
    execution_interval_sec = request.interval_sec
    exec_bars: dict[str, list[Any]] = {}
    if request.interval_sec == 86400 and _trailing_enabled:
        # Probe finest intraday grain. A missing/absent
        # ``stocks.intraday_bars`` Iceberg table (or any catalog/query
        # failure) MUST NOT crash the run — collapse to pure-daily.
        try:
            cov = intraday_coverage(
                tickers=universe,
                period_start=request.period_start,
                period_end=request.period_end,
            )
        except Exception:
            _logger.warning(
                "two-clock: intraday coverage probe failed; "
                "daily fallback",
                exc_info=True,
            )
            cov = {}
        grains = {
            c.finest_interval_sec
            for c in cov.values()
            if c.finest_interval_sec is not None
        }
        if grains:
            execution_interval_sec = min(grains)  # 900 today
            covered = [
                t
                for t, c in cov.items()
                if c.finest_interval_sec == execution_interval_sec
            ]
            exec_bars = load_intraday_bars_window(
                tickers=covered,
                interval_sec=execution_interval_sec,
                period_start=request.period_start,
                period_end=request.period_end,
                warmup_days=0,
            )
    # A ticker is "exec-covered" iff it has 15m bars in ``exec_bars``.
    # Covered tickers route trailing through ``exec_sim`` and are
    # evaluated on every execution bar. Uncovered tickers (in the
    # universe but with no intraday coverage) fall back to the LEGACY
    # daily ``_trailing_managers`` path — evaluated only on signal
    # bars against the daily ``bars`` dict, filling via the daily
    # ``sim``. Both paths may coexist in one run. ``daily_fallback_
    # tickers`` is consumed by Task 6 (BacktestSummary field).
    exec_covered: set[str] = set(exec_bars.keys())
    daily_fallback_tickers = [
        t for t in universe if t not in exec_covered
    ]
    if exec_bars:
        _logger.debug(
            "two-clock: execution_interval_sec=%d, exec-covered=%d, "
            "daily-fallback=%d",
            execution_interval_sec,
            len(exec_bars),
            len(daily_fallback_tickers),
        )

    # ASETPLTFRM-477 / PRE-3 Task 1 — per-exec-bar intraday
    # FEATURES (incl. ``rsi_2``) for exec-covered tickers, so a
    # later task can evaluate entries against the execution clock
    # (mirroring live R1's OR-trigger), rather than daily-close
    # only. Loaded ONLY for two-clock DAILY-signal runs (this
    # whole setup block is gated on ``request.interval_sec ==
    # 86400``, i.e. ``not is_intraday``) — the native-intraday
    # branch above already loads its own feature panel and the
    # non-two-clock daily path is untouched. Uncovered tickers
    # (``daily_fallback_tickers``) are skipped — they're not in
    # ``exec_covered`` and keep the once/day daily-close entry.
    # Indexed flat by ``(ticker, ts_ns)`` per the design spec
    # (§3.1) so Task 2 can do a single dict lookup per exec bar.
    exec_intraday_features: dict[tuple[str, int], dict[str, Any]] = {}
    if exec_covered and not is_intraday:
        try:
            _exec_feature_panel = load_intraday_features_window(
                tickers=sorted(exec_covered),
                interval_sec=execution_interval_sec,
                period_start=request.period_start,
                period_end=request.period_end,
            )
        except FeaturePanelMissingError as exc:
            # A missing/not-yet-backfilled feature panel MUST NOT
            # crash the run — Task 2 will find no entries for
            # these tickers at the exec grain; they still get the
            # existing daily-close signal-bar evaluation, so this
            # degrades gracefully rather than failing closed. A
            # genuine code/config bug (TypeError, bad kwarg,
            # Iceberg-catalog wiring error) is NOT this exception
            # type and propagates loudly, matching the two existing
            # ``load_intraday_features_window`` call sites above
            # (~L198-215) — narrow on purpose, not ``Exception``.
            _logger.warning(
                "two-clock: intraday feature panel missing for "
                "exec-covered tickers (interval_sec=%d, %s..%s); "
                "entries stay daily-close-only: %s",
                execution_interval_sec,
                request.period_start,
                request.period_end,
                exc,
                exc_info=True,
            )
            _exec_feature_panel = {}
        for _ticker, _rows in _exec_feature_panel.items():
            for _ts_ns, _feat in _rows.items():
                exec_intraday_features[(_ticker, _ts_ns)] = _feat

    # ExecutionSimulator owns the per-ticker TrailingStopManager
    # lifecycle in two-clock mode (same class drives live, so
    # behaviour cannot diverge). Instantiated always; only USED
    # when ``two_clock`` is True.
    exec_sim = ExecutionSimulator(strategy.risk.per_trade)

    two_clock = bool(exec_bars)
    exec_timeline: list[tuple[date, int]] = []
    exec_by_ts: dict[str, dict[int, Any]] = {}
    signal_bar_keys: set[tuple[date, int]] = set()
    exec_sim_broker: SimBroker | None = None
    if two_clock:
        exec_timeline = sorted(
            {
                (b.date, b.bar_open_ts_ns)
                for bl in exec_bars.values()
                for b in bl
                if b.bar_open_ts_ns is not None
            },
            key=lambda x: (x[1] or 0, x[0]),
        )
        exec_by_ts = {
            t: {
                b.bar_open_ts_ns: b
                for b in bl
                if b.bar_open_ts_ns is not None
            }
            for t, bl in exec_bars.items()
        }
        # The LAST execution bar of each trading day is that day's
        # daily signal bar — AST/entry evaluation runs only there.
        last_ns_of_day: dict[date, int] = {}
        for d, ns in exec_timeline:
            if ns is not None:
                last_ns_of_day[d] = max(last_ns_of_day.get(d, ns), ns)
        signal_bar_keys = {
            (d, ns) for d, ns in last_ns_of_day.items()
        }
        # Exit fills must resolve on the CURRENT execution bar via
        # the trigger-price path, so the exit broker is built from
        # the intraday bars. Entries keep the daily ``sim``.
        exec_sim_broker = SimBroker(
            bars=exec_bars,
            fee_as_of=request.period_start,
            adtv_lookup=adtv_lookup,
        )

    # In two-clock mode the runner walks the execution timeline; the
    # daily signal timeline is reduced to the per-day signal bars.
    walk_timeline: list[tuple[date, int | None]] = (
        exec_timeline if two_clock else timeline  # type: ignore[assignment]
    )

    for bar_date, ts_ns in walk_timeline:
        # In two-clock mode AST/signal evaluation runs only on the
        # day's signal bar; exit checks run on every execution bar.
        is_signal_bar = (
            (not two_clock) or (bar_date, ts_ns) in signal_bar_keys
        )
        # Warmup-only bars feed the indicator engine but never
        # see strategy evaluation — the user asked to backtest
        # period_start..period_end, not the warmup range.
        if bar_date < request.period_start:
            continue
        if bar_date != last_bar_date:
            day_start_realised = pt.total_realised_pnl_inr()
            last_bar_date = bar_date
        # IST clock-time for the MIS entry-cutoff gate. Daily
        # runs (ts_ns is None) return None and the gate no-ops.
        bar_ist_time = ist_time_from_ns(ts_ns) if is_intraday else None

        # Stop-loss enforcement (universal v1, long-only). Run
        # BEFORE per-ticker AST eval so an in-flight stop blocks
        # any conflicting AST action on the same bar. The monitor
        # is pure (see backend/algo/backtest/stop_loss_monitor.py);
        # this loop translates triggers into SELL OrderIntents.
        open_pos_now = pt.open_positions()
        closes_this_bar: dict[str, Decimal] = {}
        lows_this_bar: dict[str, Decimal] = {}
        highs_this_bar: dict[str, Decimal] = {}
        if two_clock:
            # Exit checks + intraday marks resolve against the
            # current EXECUTION bar (15m), not the daily signal bar.
            for _t, _ts_map in exec_by_ts.items():
                _cur = _ts_map.get(ts_ns)
                if _cur is not None:
                    closes_this_bar[_t] = _cur.close
                    lows_this_bar[_t] = _cur.low
                    highs_this_bar[_t] = _cur.high
        else:
            for _t, _blist in bars.items():
                if is_intraday:
                    _cur = bars_by_ts.get(_t, {}).get(ts_ns)
                else:
                    _cur = next(
                        (b for b in _blist if b.date == bar_date),
                        None,
                    )
                if _cur is not None:
                    closes_this_bar[_t] = _cur.close
                    lows_this_bar[_t] = _cur.low
                    highs_this_bar[_t] = _cur.high
        stop_loss_skip: set[str] = set()
        if not _trailing_enabled:
            # Flat %-stop (v1/v2/v3 unchanged).
            stop_triggers = check_stop_loss_triggers(
                open_positions={
                    t: {"qty": p.qty, "avg_price": p.avg_price}
                    for t, p in open_pos_now.items()
                },
                current_closes=closes_this_bar,
                stop_loss_pct=float(
                    strategy.risk.per_trade.stop_loss_pct
                ),
            )
            for trig in stop_triggers:
                existing = open_pos_now.get(trig.ticker)
                if existing is None or existing.qty <= 0:
                    continue
                sl_intent = OrderIntent(
                    ticker=trig.ticker,
                    side="SELL",
                    qty=existing.qty,
                    intent_emitted_at=bar_date,
                    intent_emitted_ts_ns=ts_ns,
                    exit_reason="stop_loss",
                )
                try:
                    sl_fill = sim.execute(sl_intent)
                except NoBarAvailableError:
                    sl_fill = None
                if sl_fill is None:
                    continue
                pt.apply_fill(sl_fill)
                total_fees += sl_fill.fees_inr
                fee_rates_version = sl_fill.fee_rates_version
                events.append(
                    event_row(
                        session_id=session_id,
                        user_id=user_id,
                        strategy_id=strategy.id,
                        mode="backtest",
                        type_="order_filled",
                        payload={
                            "ticker": sl_fill.ticker,
                            "side": sl_fill.side,
                            "qty": sl_fill.qty,
                            "fill_price": str(sl_fill.fill_price),
                            "fill_date": (
                                sl_fill.fill_date.isoformat()
                            ),
                            "fees_inr": str(sl_fill.fees_inr),
                            "fee_rates_version": (
                                sl_fill.fee_rates_version
                            ),
                            "exit_reason": "stop_loss",
                        },
                    )
                )
                stop_loss_skip.add(trig.ticker)
                _logger.debug(
                    "stop_loss trigger %s avg=%.4f close=%.4f "
                    "loss=%.2f%% stop=%.2f%%",
                    trig.ticker,
                    float(trig.avg_price),
                    float(trig.current_close),
                    float(trig.loss_pct),
                    float(trig.stop_loss_pct),
                )
        else:
            # v5 trailing stop evaluation. Two routing paths may
            # coexist in one run:
            #   • LEGACY daily ``_trailing_managers`` — pure-daily
            #     runs, AND two-clock tickers with NO 15m coverage
            #     (``daily_fallback_tickers``). Evaluated only on
            #     signal bars against the daily ``bars`` dict, filling
            #     via the daily ``sim``. Byte-identical to the
            #     pre-two-clock engine in pure-daily mode.
            #   • Two-clock ``exec_sim`` — exec-covered tickers,
            #     evaluated on every 15m execution bar.
            # Legacy path: skip entirely on non-signal exec bars
            # (two-clock) so uncovered tickers exit at the daily grain.
            if (not two_clock) or is_signal_bar:
                _legacy_mgrs = list(_trailing_managers.items())
            else:
                _legacy_mgrs = []
            for _t, _mgr in _legacy_mgrs:
                _pos = open_pos_now.get(_t)
                if _pos is None or _pos.qty <= 0:
                    del _trailing_managers[_t]
                    continue
                if two_clock:
                    # Uncovered ticker on a signal bar: source the
                    # day's DAILY low/high (the exec-bar maps only
                    # carry covered tickers).
                    _dbar = next(
                        (
                            b
                            for b in bars.get(_t, [])
                            if b.date == bar_date
                        ),
                        None,
                    )
                    _bar_low = _dbar.low if _dbar is not None else None
                    _bar_high = (
                        _dbar.high if _dbar is not None else None
                    )
                else:
                    _bar_low = lows_this_bar.get(_t)
                    _bar_high = highs_this_bar.get(_t)
                if _bar_low is None or _bar_high is None:
                    continue
                # Check stop-hit via LOW first.
                if float(_bar_low) <= _mgr.current_stop:
                    _stop_ev = _mgr.on_price_update(float(_bar_low))
                    if (
                        _stop_ev is None
                        or _stop_ev.event_type != "STOP_HIT"
                    ):
                        # Ratchet updated stop but gap didn't clear;
                        # update HWM from HIGH and continue.
                        _mgr.on_price_update(float(_bar_high))
                        continue
                else:
                    # No stop hit — advance HWM with bar HIGH.
                    _mgr.on_price_update(float(_bar_high))
                    continue
                # Map phase → exit_reason.
                _exit_reason = (
                    "trail_stop"
                    if _stop_ev.phase.value == 2
                    else "phase1_ratchet"
                    if _stop_ev.phase.value == 15
                    else "phase1_stop"
                )
                # Legacy fills on the daily ``sim`` (T+1 daily open).
                # In two-clock mode pass ts_ns=None so the intent uses
                # the daily date-keyed path — the daily ``sim`` has no
                # exec ts_ns index and would otherwise never fill.
                _tr_intent = OrderIntent(
                    ticker=_t,
                    side="SELL",
                    qty=_pos.qty,
                    intent_emitted_at=bar_date,
                    intent_emitted_ts_ns=None if two_clock else ts_ns,
                    exit_reason=_exit_reason,
                )
                try:
                    _tr_fill = sim.execute(_tr_intent)
                except NoBarAvailableError:
                    _tr_fill = None
                if _tr_fill is None:
                    continue
                pt.apply_fill(_tr_fill)
                total_fees += _tr_fill.fees_inr
                fee_rates_version = _tr_fill.fee_rates_version
                events.append(
                    event_row(
                        session_id=session_id,
                        user_id=user_id,
                        strategy_id=strategy.id,
                        mode="backtest",
                        type_="order_filled",
                        payload={
                            "ticker": _tr_fill.ticker,
                            "side": _tr_fill.side,
                            "qty": _tr_fill.qty,
                            "fill_price": str(_tr_fill.fill_price),
                            "fill_date": (
                                _tr_fill.fill_date.isoformat()
                            ),
                            "fees_inr": str(_tr_fill.fees_inr),
                            "fee_rates_version": (
                                _tr_fill.fee_rates_version
                            ),
                            "exit_reason": _exit_reason,
                            "trailing_phase": (
                                _stop_ev.phase.value
                            ),
                            "trailing_hwm": _stop_ev.hwm,
                        },
                    )
                )
                del _trailing_managers[_t]
                stop_loss_skip.add(_t)
                _logger.debug(
                    "trailing_stop %s phase=%d stop=%.4f "
                    "bar_low=%.4f exit=%s",
                    _t,
                    _stop_ev.phase.value,
                    _stop_ev.new_stop,
                    float(_bar_low),
                    _exit_reason,
                )
            # Two-clock trailing exits — exec-covered tickers only,
            # evaluated on EVERY 15m execution bar via the
            # ExecutionSimulator. The same TrailingStopManager class
            # drives live, so behaviour cannot diverge. Exit fills
            # resolve on the CURRENT execution bar via the
            # trigger-price path.
            if two_clock:
                for _t in list(open_pos_now.keys()):
                    if not exec_sim.has(_t):
                        continue
                    _lo = lows_this_bar.get(_t)
                    _hi = highs_this_bar.get(_t)
                    if _lo is None or _hi is None:
                        continue
                    _dec = exec_sim.evaluate_bar(_t, _lo, _hi)
                    if _dec is None:
                        continue
                    _pos = open_pos_now[_t]
                    _intent = OrderIntent(
                        ticker=_t,
                        side="SELL",
                        qty=_pos.qty,
                        intent_emitted_at=bar_date,
                        intent_emitted_ts_ns=ts_ns,
                        exit_reason=_dec.exit_reason,
                        trigger_price=_dec.trigger_price,
                        # Fee product reflects the strategy's TRUE
                        # product, not the exec-bar grain. A CNC daily
                        # strategy's intraday-detected exit is still a
                        # delivery sell.
                        product=(
                            "DELIVERY"
                            if strategy.product == "CNC"
                            else "INTRADAY"
                        ),
                    )
                    try:
                        _fill = (
                            exec_sim_broker.execute(_intent)
                            if exec_sim_broker is not None
                            else None
                        )
                    except NoBarAvailableError:
                        _fill = None
                    if _fill is None:
                        continue
                    pt.apply_fill(_fill)
                    total_fees += _fill.fees_inr
                    fee_rates_version = _fill.fee_rates_version
                    events.append(
                        event_row(
                            session_id=session_id,
                            user_id=user_id,
                            strategy_id=strategy.id,
                            mode="backtest",
                            type_="order_filled",
                            payload={
                                "ticker": _fill.ticker,
                                "side": _fill.side,
                                "qty": _fill.qty,
                                "fill_price": str(_fill.fill_price),
                                "fill_date": (
                                    _fill.fill_date.isoformat()
                                ),
                                "fees_inr": str(_fill.fees_inr),
                                "fee_rates_version": (
                                    _fill.fee_rates_version
                                ),
                                "exit_reason": _dec.exit_reason,
                                "trailing_phase": _dec.phase,
                                "trailing_hwm": _dec.hwm,
                                "trigger_price": str(
                                    _dec.trigger_price
                                ),
                                "execution_interval_sec": (
                                    execution_interval_sec
                                ),
                            },
                        )
                    )
                    exec_sim.drop(_t)
                    stop_loss_skip.add(_t)
                    _logger.debug(
                        "two_clock trailing %s phase=%d trigger=%.4f "
                        "bar_low=%.4f exit=%s",
                        _t,
                        _dec.phase,
                        float(_dec.trigger_price),
                        float(_lo),
                        _dec.exit_reason,
                    )

        # Time-based stop (ASETPLTFRM-430 Exp.3). Same pattern as
        # the price stop above but triggers on holding_days
        # exceeding strategy.risk.per_trade.max_holding_days. For
        # mean-reversion strategies whose reversion window is fixed
        # (Connors RSI(2) at 2-5 days); fires AFTER the window
        # without truncating the price action inside it.
        time_triggers = check_time_stop_triggers(
            open_positions={
                t: {"qty": p.qty, "opened_at": p.opened_at}
                for t, p in open_pos_now.items()
                if t not in stop_loss_skip
            },
            current_date=bar_date,
            max_holding_days=(
                strategy.risk.per_trade.max_holding_days
            ),
        )
        for trig in time_triggers:
            existing = open_pos_now.get(trig.ticker)
            if existing is None or existing.qty <= 0:
                continue
            ts_intent = OrderIntent(
                ticker=trig.ticker,
                side="SELL",
                qty=existing.qty,
                intent_emitted_at=bar_date,
                intent_emitted_ts_ns=ts_ns,
                exit_reason="time_stop",
                # Two-clock exits route through exec_sim_broker (exec
                # bar grain) — book fees against the strategy's TRUE
                # product so a CNC daily strategy bills DELIVERY.
                product=(
                    "DELIVERY"
                    if strategy.product == "CNC"
                    else "INTRADAY"
                ),
            )
            # Two-clock: market exits fill on the next EXECUTION bar
            # (the daily ``sim`` has no exec ts_ns index → would never
            # fill). No trigger_price → normal next-exec-bar-open path.
            _ts_broker = (
                exec_sim_broker
                if two_clock and exec_sim_broker is not None
                else sim
            )
            try:
                ts_fill = _ts_broker.execute(ts_intent)
            except NoBarAvailableError:
                ts_fill = None
            if ts_fill is None:
                continue
            pt.apply_fill(ts_fill)
            total_fees += ts_fill.fees_inr
            fee_rates_version = ts_fill.fee_rates_version
            events.append(
                event_row(
                    session_id=session_id,
                    user_id=user_id,
                    strategy_id=strategy.id,
                    mode="backtest",
                    type_="order_filled",
                    payload={
                        "ticker": ts_fill.ticker,
                        "side": ts_fill.side,
                        "qty": ts_fill.qty,
                        "fill_price": str(ts_fill.fill_price),
                        "fill_date": ts_fill.fill_date.isoformat(),
                        "fees_inr": str(ts_fill.fees_inr),
                        "fee_rates_version": (
                            ts_fill.fee_rates_version
                        ),
                        "exit_reason": "time_stop",
                    },
                )
            )
            stop_loss_skip.add(trig.ticker)
            _logger.debug(
                "time_stop trigger %s held=%d days "
                "(threshold=%d)",
                trig.ticker,
                trig.holding_days,
                trig.max_holding_days,
            )

        # ASETPLTFRM-435 v4 — mid-trade regime exit. Re-evaluates
        # the strategy's mid_trade_regime_check condition against
        # the current bar's market features. If the condition is
        # False, force-close ALL open positions at next-bar-open
        # (same fill semantics as stop_loss / time_stop) with
        # exit_reason="regime_exit". No-op if the field is None.
        mtre_check = getattr(
            strategy, "mid_trade_regime_check", None,
        )
        if mtre_check is not None:
            open_for_regime = pt.open_positions()
            if open_for_regime:
                # Assemble market-only features for the bar — same
                # sources the entry-time regime gate uses. Skip
                # per-ticker / factor inputs (regime check should
                # only reference market-level features).
                market_feats: dict[str, Decimal] = {}
                _mr = market_regime.get(bar_date)
                market_feats["nifty_above_sma200"] = (
                    _mr if _mr is not None else Decimal("0")
                )
                _mt = market_trend.get(bar_date)
                market_feats["nifty_30d_return_pct"] = (
                    _mt if _mt is not None else Decimal("0")
                )
                _mds = market_dist_sma200.get(bar_date)
                market_feats["nifty_distance_from_sma200_pct"] = (
                    _mds if _mds is not None else Decimal("0")
                )
                _rr = regime_by_date.get(bar_date)
                if _rr:
                    # regime_by_date returns dict with regime_label
                    # + stress_prob; merge in directly.
                    market_feats.update(_rr)
                mtre_triggers = check_regime_exit_triggers(
                    open_positions={
                        t: {"qty": p.qty}
                        for t, p in open_for_regime.items()
                        if t not in stop_loss_skip
                    },
                    bar_date=bar_date,
                    market_features=market_feats,
                    regime_check=mtre_check.model_dump(
                        by_alias=True,
                    ),
                )
                for trig in mtre_triggers:
                    existing = open_for_regime.get(trig.ticker)
                    if existing is None or existing.qty <= 0:
                        continue
                    re_intent = OrderIntent(
                        ticker=trig.ticker,
                        side="SELL",
                        qty=existing.qty,
                        intent_emitted_at=bar_date,
                        intent_emitted_ts_ns=ts_ns,
                        exit_reason="regime_exit",
                        # Two-clock exits route through
                        # exec_sim_broker — book fees against the
                        # strategy's TRUE product (CNC → DELIVERY).
                        product=(
                            "DELIVERY"
                            if strategy.product == "CNC"
                            else "INTRADAY"
                        ),
                    )
                    # Two-clock: market exit fills on the next
                    # EXECUTION bar (daily ``sim`` lacks the exec
                    # ts_ns index → would silently never fill).
                    _re_broker = (
                        exec_sim_broker
                        if two_clock and exec_sim_broker is not None
                        else sim
                    )
                    try:
                        re_fill = _re_broker.execute(re_intent)
                    except NoBarAvailableError:
                        re_fill = None
                    if re_fill is None:
                        continue
                    pt.apply_fill(re_fill)
                    total_fees += re_fill.fees_inr
                    fee_rates_version = re_fill.fee_rates_version
                    events.append(
                        event_row(
                            session_id=session_id,
                            user_id=user_id,
                            strategy_id=strategy.id,
                            mode="backtest",
                            type_="order_filled",
                            payload={
                                "ticker": re_fill.ticker,
                                "side": re_fill.side,
                                "qty": re_fill.qty,
                                "fill_price": str(
                                    re_fill.fill_price,
                                ),
                                "fill_date": (
                                    re_fill.fill_date.isoformat()
                                ),
                                "fees_inr": str(re_fill.fees_inr),
                                "fee_rates_version": (
                                    re_fill.fee_rates_version
                                ),
                                "exit_reason": "regime_exit",
                            },
                        )
                    )
                    stop_loss_skip.add(trig.ticker)
                    regime_exit_count += 1
                if mtre_triggers:
                    _logger.debug(
                        "regime_exit force-closed %d positions on "
                        "%s",
                        len(mtre_triggers), bar_date.isoformat(),
                    )

        # ASETPLTFRM-434 Exp.2 — cooldown gate input. The function
        # is pure; we hoist the closed-positions snapshot once per
        # outer bar so the per-ticker loop is O(1) per call (the
        # in_cooldown scan walks the list anyway, but reading the
        # snapshot inside the loop avoids a stale-during-iteration
        # surprise if the loop ever mutates pt._closed mid-bar).
        cooldown_days = strategy.risk.per_trade.cooldown_after_failed_exit_days
        closed_for_cooldown = (
            pt.closed_positions() if cooldown_days else []
        )

        # Two-clock marks: refresh last_close from the current
        # execution bar so the intraday equity curve tracks the
        # 15m path even on non-signal bars (note 6). On signal
        # bars the per-ticker entry loop below also refreshes from
        # the daily bar — same close, so no conflict.
        if two_clock:
            for _t, _c in closes_this_bar.items():
                last_close[_t] = _c

        # AST/signal evaluation (entries + rebalances) fires once
        # per trading day — on the daily signal bar. Exit checks
        # above already ran on this execution bar. ``is_signal_bar``
        # is always True outside two-clock mode, so the daily and
        # intraday paths are unchanged.
        for ticker in (universe if is_signal_bar else []):
            if ticker in stop_loss_skip:
                continue
            # ASETPLTFRM-434 Exp.2 — pre-AST repeat-offender gate.
            # When set, skip entries on tickers whose most recent
            # failed exit (time_stop / stop_loss) is within the
            # configured cooldown window.
            if cooldown_days and in_cooldown(
                ticker=ticker,
                bar_date=bar_date,
                closed_positions=closed_for_cooldown,
                cooldown_days=cooldown_days,
            ):
                cooldown_skip_count += 1
                continue
            blist = bars.get(ticker)
            if not blist:
                continue
            if is_intraday:
                current = bars_by_ts.get(ticker, {}).get(ts_ns)
            else:
                current = next(
                    (b for b in blist if b.date == bar_date),
                    None,
                )
            if current is None:
                continue
            # Refresh the mark-to-market price for this ticker
            # before any strategy logic runs on the bar — the
            # end-of-day equity snapshot below uses last_close.
            last_close[ticker] = current.close
            open_pos = pt.open_positions().get(ticker)
            # ASETPLTFRM-400 slice 4b — intraday strategies look
            # up per-bar indicators by ``ts_ns``; daily strategies
            # keep the date-keyed path. Both branches fall back to
            # ``today_ltp`` / ``today_vol`` for warmup-period bars
            # where SMA200/RSI haven't settled yet.
            if is_intraday:
                bar_feats = intraday_indicators.get(
                    ticker,
                    {},
                ).get(ts_ns) or {
                    "today_ltp": current.close,
                    "today_vol": Decimal(current.volume),
                }
            else:
                bar_feats = indicators.get(ticker, {}).get(
                    bar_date,
                    {
                        "today_ltp": current.close,
                        "today_vol": Decimal(current.volume),
                    },
                )
            # FE-15b — shared per-bar feature assembly (single
            # source of truth across backtest/paper/live). Daily
            # overlay (interval_sec=86400) injected under {_1d}
            # keys for intraday strategies; empty for daily.
            ticker_features = assemble_per_bar_features(
                bar_feats=bar_feats,
                market_regime=market_regime.get(bar_date),
                market_trend=market_trend.get(bar_date),
                market_dist_sma200=market_dist_sma200.get(bar_date),
                factor_row=factors_by_key.get((ticker, bar_date)),
                regime_row=regime_by_date.get(bar_date),
                daily_overlay=lookup_daily_overlay(
                    daily_panel=daily_overlay_panel,
                    ticker=ticker,
                    bar_date=bar_date,
                ),
            )
            ctx = EvalContext(
                ticker=ticker,
                bar_date=bar_date,
                features=ticker_features,
                open_qty=open_pos.qty if open_pos else 0,
            )
            try:
                action = evaluator.eval_node(
                    strategy.root.model_dump(by_alias=True),
                    ctx,
                )
            except KeyError as _ke:
                _key_err_counts[str(_ke)] = (
                    _key_err_counts.get(str(_ke), 0) + 1
                )
                continue
            except Exception:  # pragma: no cover
                # Defensive — anything else (typos, bad literals)
                # gets reported as a feature-key-error so the user
                # can see it on the run summary line.
                _key_err_counts["eval-exception"] = (
                    _key_err_counts.get("eval-exception", 0) + 1
                )
                continue
            else:
                # Strategies whose root is a bare condition (`and`,
                # `or`, `compare`) return a bool. There's no buy
                # action attached — treat as no-op rather than
                # crashing in `_action_to_intent`. Surface in the
                # run summary as a configuration warning.
                if not isinstance(action, dict):
                    _key_err_counts["bool-root-no-buy-action"] = (
                        _key_err_counts.get(
                            "bool-root-no-buy-action",
                            0,
                        )
                        + 1
                    )
                    continue
            current_equity = (
                request.initial_capital_inr
                + pt.total_realised_pnl_inr()
                - total_fees
            )
            # REGIME-4 — assemble sizing context for new modes.
            # Legacy {shares}/{notional_inr} bypass this block.
            factor_row = factors_by_key.get((ticker, bar_date), {})
            realized_vol = factor_row.get(
                "realized_vol_60d",
                Decimal("NaN"),
            )
            sizing_ctx = SizingContext(
                ticker=ticker,
                bar_date=bar_date,
                nav=current_equity,
                cash=current_equity,
                stock_price=current.close,
                realized_vol_annual=realized_vol,
                sector=None,
                sector_exposure=Decimal("0"),
                equity_curve=[
                    (p.bar_date, p.equity_inr) for p in equity_points
                ],
            )
            # Two-clock entries fill on the DAILY ``sim`` (daily
            # signal clock), so the intent must key off the daily
            # date path — pass ts_ns=None so SimBroker resolves
            # the next daily bar's open (T+1), not an exec bar.
            _entry_ts_ns = None if two_clock else ts_ns
            intent = _action_to_intent(
                action,
                ticker=ticker,
                bar_date=bar_date,
                pt=pt,
                last_price=current.close,
                current_equity=current_equity,
                sizing_ctx=sizing_ctx,
                bar_open_ts_ns=_entry_ts_ns,
            )
            if intent is None:
                continue

            # MIS "no new entries after T-1h" gate. SELL / exit
            # intents stay allowed — closing a position is always
            # OK, especially close to square-off.
            if (
                intent.side == "BUY"
                and is_intraday
                and is_mis
                and bar_ist_time is not None
                and not is_entry_allowed(
                    product=strategy.product,
                    entry_cutoff_raw=strategy.entry_cutoff_time,
                    bar_time_ist=bar_ist_time,
                )
            ):
                rejected_count += 1
                events.append(
                    event_row(
                        session_id=session_id,
                        user_id=user_id,
                        strategy_id=strategy.id,
                        mode="backtest",
                        type_="signal_rejected",
                        payload={
                            "ticker": intent.ticker,
                            "side": intent.side,
                            "qty": intent.qty,
                            "reason": "mis_entry_cutoff",
                            "bar_ist_time": (bar_ist_time.isoformat()),
                            "entry_cutoff": (strategy.entry_cutoff_time),
                        },
                    )
                )
                continue

            # 3-tier RiskEngine gate (per-trade / daily / portfolio)
            # — same logic that PaperRuntime uses, so a strategy
            # behaves identically across backtest and paper.
            open_qty_map = {t: p.qty for t, p in pt.open_positions().items()}
            day_realised = pt.total_realised_pnl_inr() - day_start_realised
            account_state = AccountState(
                user_id=user_id,
                day_date=bar_date,
                initial_capital_inr=request.initial_capital_inr,
                current_equity_inr=current_equity,
                daily_realised_pnl_inr=day_realised,
                daily_unrealised_pnl_inr=Decimal("0"),
                open_positions=open_qty_map,
                open_position_count=len(open_qty_map),
                kill_switch_active=False,
            )
            signal = Signal(
                strategy_id=strategy.id,
                user_id=user_id,
                ticker=intent.ticker,
                side=intent.side,
                qty=intent.qty,
                emitted_at_ns=(
                    ts_ns
                    if ts_ns is not None
                    else int(
                        datetime(
                            bar_date.year,
                            bar_date.month,
                            bar_date.day,
                            tzinfo=timezone.utc,
                        ).timestamp()
                        * 1_000_000_000
                    )
                ),
            )
            decision = risk.gate(
                signal=signal,
                account=account_state,
                risk=risk_payload,
                last_price=current.close,
            )
            if decision.outcome == "reject":
                rejected_count += 1
                events.append(
                    event_row(
                        session_id=session_id,
                        user_id=user_id,
                        strategy_id=strategy.id,
                        mode="backtest",
                        type_="signal_rejected",
                        payload={
                            "ticker": intent.ticker,
                            "side": intent.side,
                            "qty": intent.qty,
                            "reason": (
                                decision.reason.value
                                if decision.reason
                                else "unknown"
                            ),
                            "threshold": (
                                str(decision.threshold)
                                if decision.threshold is not None
                                else None
                            ),
                            "observed_value": (
                                str(decision.observed_value)
                                if decision.observed_value is not None
                                else None
                            ),
                        },
                    )
                )
                continue
            if (
                decision.outcome == "scale"
                and decision.adjusted_qty
                and decision.adjusted_qty > 0
                and decision.adjusted_qty < intent.qty
            ):
                scaled_count += 1
                intent = intent.model_copy(
                    update={"qty": decision.adjusted_qty},
                )

            try:
                fill = sim.execute(intent)
            except NoBarAvailableError:
                continue
            if fill is None:
                continue

            pt.apply_fill(fill)
            total_fees += fill.fees_inr
            fee_rates_version = fill.fee_rates_version

            # v5 trailing: init manager on BUY; clean up on SELL.
            # ATR computed from raw bars — compute_indicators only
            # produces RSI/SMA; atr_14 lives in the factor store.
            # Routing is PER-TICKER on exec-coverage: an exec-covered
            # ticker (15m bars present) owns its manager in
            # ``exec_sim`` (evaluated every exec bar); an uncovered
            # ticker uses the LEGACY ``_trailing_managers`` daily
            # path. In pure-daily mode ``exec_covered`` is empty, so
            # every ticker takes the legacy path (byte-identical).
            # The same ``_atr`` value feeds both so geometry can't
            # diverge.
            if _trailing_enabled:
                _t_covered = fill.ticker in exec_covered
                if fill.side == "BUY":
                    _blist = bars.get(fill.ticker, [])
                    _bars_up = [
                        b for b in _blist if b.date <= bar_date
                    ]
                    _atr_series = _wilder_atr(_bars_up, 14)
                    _atr = float(
                        _atr_series[-1]
                        if _atr_series and _atr_series[-1] is not None
                        else 0.0
                    )
                    _has_mgr = (
                        exec_sim.has(fill.ticker)
                        if _t_covered
                        else fill.ticker in _trailing_managers
                    )
                    if _atr > 0 and not _has_mgr:
                        # One manager per position — don't overwrite
                        # when accumulating lots (averaging-in doesn't
                        # reset the stop set at first entry).
                        if _t_covered:
                            exec_sim.on_buy_fill(
                                fill.ticker,
                                float(fill.fill_price),
                                _atr,
                            )
                        else:
                            _trailing_managers[fill.ticker] = (
                                TrailingStopManager(
                                    strategy.risk.per_trade,
                                    entry_price=float(fill.fill_price),
                                    atr=_atr,
                                    ticker=fill.ticker,
                                )
                            )
                    else:
                        _logger.warning(
                            "trailing: insufficient bars for "
                            "atr_14 on %s at %s — skip manager",
                            fill.ticker, bar_date,
                        )
                elif fill.side == "SELL":
                    if _t_covered:
                        exec_sim.drop(fill.ticker)
                    else:
                        _trailing_managers.pop(fill.ticker, None)

            events.append(
                event_row(
                    session_id=session_id,
                    user_id=user_id,
                    strategy_id=strategy.id,
                    mode="backtest",
                    type_="order_filled",
                    payload={
                        "ticker": fill.ticker,
                        "side": fill.side,
                        "qty": fill.qty,
                        "fill_price": str(fill.fill_price),
                        "fill_date": fill.fill_date.isoformat(),
                        "fees_inr": str(fill.fees_inr),
                        "fee_rates_version": fill.fee_rates_version,
                    },
                )
            )

            # ASETPLTFRM-402 / FE-5 — per-fill feature
            # snapshot for the alpha-research dataset.
            # ADDITIVE: snapshot write happens AFTER the
            # order_filled event so the promotion gate's
            # algo.events scan + paper-completion stats
            # are bit-for-bit unchanged. Wrapped in
            # try/except + logged with exc_info; snapshot
            # failure never blocks the fill or the event.
            try:
                from backend.algo.features.snapshots import (
                    write_trade_feature_snapshot,
                )

                _snap_fill_id = f"{session_id}:{fill.ticker}:{fill.intent_id}"
                write_trade_feature_snapshot(
                    fill_id=_snap_fill_id,
                    run_id=str(session_id),
                    strategy_id=str(strategy.id),
                    ticker=fill.ticker,
                    side=fill.side,
                    qty=fill.qty,
                    fill_price=fill.fill_price,
                    fill_ts_ns=(
                        fill.fill_ts_ns
                        if fill.fill_ts_ns is not None
                        else ts_ns
                    ),
                    bar_date=fill.fill_date.isoformat(),
                    mode="backtest",
                    features=ticker_features,
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "trade_feature_snapshot hook failed "
                    "(non-fatal): ticker=%s mode=backtest "
                    "ts_ns=%s",
                    fill.ticker,
                    ts_ns,
                )

        # End-of-day equity snapshot. last_close holds the
        # most-recent close at-or-before today for each ticker
        # we've seen, so unrealised P&L on open positions
        # tracks the actual market path day-by-day instead of
        # being suppressed until period_end.
        marks = dict(last_close)
        equity = (
            request.initial_capital_inr
            + pt.total_realised_pnl_inr()
            + pt.unrealised_pnl_inr(marks)
            - total_fees
        )
        equity_points.append(
            EquityPoint(
                bar_date=bar_date,
                equity_inr=equity,
                # ASETPLTFRM-400 slice 5 — intraday MTM
                # granularity. Daily runs pass None (existing
                # shape); intraday runs stamp the bar's
                # ns-since-epoch so the curve is plottable
                # with intra-day resolution on the x-axis.
                bar_open_ts_ns=ts_ns,
            )
        )
        if equity > peak_equity:
            peak_equity = equity
        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity * Decimal("100")
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

        # MIS daily square-off — mirror Zerodha's auto-close at
        # the end of every trading day. Fired AFTER the equity
        # snapshot so the snapshot reflects MTM-at-day-close, then
        # positions reset for the next trading day. Marks come
        # from ``last_close`` (the bar we just walked through).
        if is_mis and (bar_date, ts_ns) in day_end_keys:
            pt.force_close_all(
                marks=dict(last_close),
                fill_date=bar_date,
                exit_reason="mis_square_off",
                fill_ts_ns=ts_ns,
            )

    # Period-end force-close. After the main loop, any position
    # still open is silently inflating ``final_equity`` via
    # unrealised P&L while ``trade_list`` stays mute. Synthetically
    # exit at the last seen close so trade_list + total_pnl
    # reconcile and the user can SEE what's been booked.
    if pt.open_positions():
        last_bar_date = (
            equity_points[-1].bar_date if equity_points else request.period_end
        )
        last_bar_ts_ns = (
            equity_points[-1].bar_open_ts_ns if equity_points else None
        )
        pt.force_close_all(
            marks=dict(last_close),
            fill_date=last_bar_date,
            exit_reason="period_end_mtm",
            fill_ts_ns=last_bar_ts_ns,
        )

    final_equity = (
        equity_points[-1].equity_inr
        if equity_points
        else request.initial_capital_inr
    )
    total_pnl = final_equity - request.initial_capital_inr
    total_pnl_pct = (
        (total_pnl / request.initial_capital_inr) * Decimal("100")
        if request.initial_capital_inr > 0
        else Decimal("0")
    )
    closed = pt.closed_positions()
    winning = sum(1 for p in closed if p.realised_pnl_inr > 0)
    losing = sum(1 for p in closed if p.realised_pnl_inr <= 0)
    win_rate = (
        Decimal(winning) / Decimal(len(closed)) * Decimal("100")
        if closed
        else Decimal("0")
    )

    trade_rows: list[TradeRow] = []
    for p in closed:
        implied_fill = (
            p.avg_price + (p.realised_pnl_inr / Decimal(p.qty))
            if p.qty > 0
            else p.avg_price
        )
        trade_rows.append(_trade_row(p, implied_fill))

    _logger.info(
        "backtest run %s: closed=%d trades, "
        "risk-rejected=%d signals, scaled=%d signals, "
        "cooldown-skips=%d, regime-exits=%d, "
        "feature-key-errors=%s",
        run_id,
        len(closed),
        rejected_count,
        scaled_count,
        cooldown_skip_count,
        regime_exit_count,
        sorted(
            _key_err_counts.items(),
            key=lambda x: -x[1],
        )[:5]
        or "none",
    )

    summary = BacktestSummary(
        run_id=run_id,
        strategy_id=strategy.id,
        status="completed",
        period_start=request.period_start,
        period_end=request.period_end,
        initial_capital_inr=request.initial_capital_inr,
        final_equity_inr=final_equity,
        total_pnl_inr=total_pnl,
        total_pnl_pct=total_pnl_pct,
        total_fees_inr=total_fees,
        total_trades=len(closed),
        winning_trades=winning,
        losing_trades=losing,
        win_rate_pct=win_rate,
        max_drawdown_pct=max_drawdown_pct,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        fee_rates_version=fee_rates_version or "n/a",
        # ASETPLTFRM-400 slice 7 — surface cadence to the UI.
        interval_sec=request.interval_sec,
        # Task 6 — execution-resolution metadata.
        execution_interval_sec=execution_interval_sec,
        daily_fallback_tickers=daily_fallback_tickers,
        equity_curve=equity_points,
        trade_list=trade_rows,
    )

    events.append(
        event_row(
            session_id=session_id,
            user_id=user_id,
            strategy_id=strategy.id,
            mode="backtest",
            type_="backtest_run_completed",
            payload=summary.model_dump(mode="json"),
        )
    )
    flush_events(events)

    # ASETPLTFRM-417 / FE-5.1 — drain the per-run feature
    # snapshot buffer in ONE Iceberg commit. Replaces the
    # FE-5 per-fill commit pattern that produced
    # ~2 manifest avros per fill (a 7,000-fill backtest blew
    # the table to 14,000 manifests / 9.4 GB on disk).
    # Buffer flush is non-fatal: failures log + return 0
    # and don't break the run summary returned to the caller.
    try:
        from backend.algo.features.snapshots_buffer import (
            get_buffer,
        )

        get_buffer().flush(
            key=(str(strategy.id), str(session_id)),
        )
    except Exception:  # noqa: BLE001
        _logger.exception(
            "[fe5.1] snapshots buffer flush failed for "
            "backtest run_id=%s (non-fatal)",
            session_id,
        )
    return summary


_NEW_SIZING_KEYS = ("vol_target_pct", "kelly_fraction")


def _action_to_intent(
    action: dict,
    *,
    ticker: str,
    bar_date,  # noqa: ANN001
    pt: PositionTracker,
    last_price: Decimal | None = None,
    current_equity: Decimal | None = None,
    sizing_ctx: SizingContext | None = None,
    bar_open_ts_ns: int | None = None,
) -> OrderIntent | None:
    """Translate an evaluator action dict to an OrderIntent (or None).

    ``last_price`` + ``current_equity`` are required only for
    ``set_target_weight`` resolution.  ``sizing_ctx`` is required
    only when the buy action uses the REGIME-4 sizing modes
    (``vol_target_pct`` / ``kelly_fraction``); legacy modes
    (``shares`` / ``notional_inr``) bypass the composer entirely
    for byte-for-byte backward compatibility.
    """
    t = action.get("type")
    if t == "buy":
        qty_spec = action["qty"]
        if sizing_ctx is not None and any(
            k in qty_spec for k in _NEW_SIZING_KEYS
        ):
            qty = compose_qty(qty_spec, sizing_ctx)
        elif "notional_inr" in qty_spec:
            # Legacy notional sizing: qty = floor(notional / price).
            # Requires last_price; falls back to no-op if missing.
            if last_price is None or last_price <= 0:
                qty = 0
            else:
                qty = int(Decimal(str(qty_spec["notional_inr"])) // last_price)
        else:
            qty = qty_spec.get("shares") or 0
        if qty <= 0:
            return None
        return OrderIntent(
            ticker=ticker,
            side="BUY",
            qty=int(qty),
            intent_emitted_at=bar_date,
            intent_emitted_ts_ns=bar_open_ts_ns,
        )
    if t == "sell":
        qty_spec = action["qty"]
        if qty_spec.get("all"):
            existing = pt.open_positions().get(ticker)
            if not existing:
                return None
            return OrderIntent(
                ticker=ticker,
                side="SELL",
                qty=existing.qty,
                intent_emitted_at=bar_date,
                intent_emitted_ts_ns=bar_open_ts_ns,
            )
        qty = qty_spec.get("shares") or 0
        if qty <= 0:
            return None
        return OrderIntent(
            ticker=ticker,
            side="SELL",
            qty=int(qty),
            intent_emitted_at=bar_date,
            intent_emitted_ts_ns=bar_open_ts_ns,
        )
    if t == "exit":
        existing = pt.open_positions().get(ticker)
        if not existing:
            return None
        return OrderIntent(
            ticker=ticker,
            side="SELL",
            qty=existing.qty,
            intent_emitted_at=bar_date,
            intent_emitted_ts_ns=bar_open_ts_ns,
        )
    if t == "set_target_weight":
        # Resolve the weight against current equity at this bar.
        # target_qty = floor(weight * equity / last_price)
        # Diff vs existing position emits a BUY (under-weight)
        # or SELL (over-weight). Equal weight is a no-op.
        if last_price is None or last_price <= 0:
            return None
        if current_equity is None or current_equity <= 0:
            return None
        try:
            weight = Decimal(str(action.get("weight", 0)))
        except Exception:  # noqa: BLE001
            return None
        if weight <= 0:
            return None
        target_notional = current_equity * weight
        target_qty = int(target_notional // last_price)
        existing = pt.open_positions().get(ticker)
        current_qty = existing.qty if existing else 0
        diff = target_qty - current_qty
        if diff > 0:
            return OrderIntent(
                ticker=ticker,
                side="BUY",
                qty=int(diff),
                intent_emitted_at=bar_date,
                intent_emitted_ts_ns=bar_open_ts_ns,
            )
        # set_target_weight is BUY-only once a position is open —
        # never trims (diff<0). Mirrors LiveRuntime/PaperRuntime's
        # _action_to_signal: recomputing target_qty from the CURRENT
        # price every bar means a plain winning move can push the
        # floor-divided target below the held qty, which is not a
        # real overweight condition — it's an artifact of dividing a
        # near-fixed target notional by a rising price. Reductions
        # come only from an explicit exit / stop_loss / time_stop /
        # regime_exit intent.
        return None
    # `hold` is an explicit no-op.
    return None
