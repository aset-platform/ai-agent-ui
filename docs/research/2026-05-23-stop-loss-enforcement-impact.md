# Stop-Loss Enforcement Framework Fix — Impact Report

| | |
|---|---|
| Date | 2026-05-23 |
| Branch | `framework/backtest-stop-loss-enforcement-spec` |
| Spec | `docs/superpowers/specs/2026-05-23-stop-loss-enforcement-design.md` |
| Plan | `docs/superpowers/plans/2026-05-23-stop-loss-enforcement.md` |
| Commits | 10 implementation commits (`54e24ec..a89c55b`) on top of spec/plan |

## 1. What shipped

`stop_loss_pct` is now enforced in all three runtimes:

| Runtime | Per-bar entry | Fill semantics | Order shape |
|---|---|---|---|
| Backtest | `runner.py` per-bar loop | Next-bar-open via `SimBroker` | SELL `OrderIntent(exit_reason="stop_loss")` |
| Paper | `runtime.py::_on_bar_close` | At-tick LTP via `PaperBroker` | SELL `Signal(reason="stop_loss")` |
| Live | `runtime.py::_on_bar_close` | Immediate aggressive LIMIT via Kite (existing `_submit_order` rails) | SELL `Signal(reason="stop_loss")` |

Shared core: a single 70-LOC pure module `backend/algo/backtest/stop_loss_monitor.py` exporting `check_stop_loss_triggers(*, open_positions, current_closes, stop_loss_pct) -> list[StopLossTrigger]`. Every runtime calls it with identical kwargs.

## 2. Framework-level verification (test coverage)

| Test layer | File | Count | What it covers |
|---|---|---:|---|
| Unit (monitor math) | `tests/test_stop_loss_monitor.py` | 8 | Trigger boundary, disabled flag, missing data, multi-position independence |
| Propagation | `tests/test_exit_reason_propagation.py` | 5 | `OrderIntent.exit_reason` → `Fill.exit_reason` → `Position.exit_reason`; SimBroker forwards intent.exit_reason |
| Backtest integration | `tests/test_stop_loss_integration.py` | 4 | Runner emits SELL at threshold breach; AST skipped on same bar; `exit_reason="stop_loss"` lands in trade list |
| Paper integration | `tests/test_paper_stop_loss_integration.py` | 5 | Paper runtime emits SELL via PaperBroker; disabled when pct=0; broker forwards Signal.reason |
| Live integration | `live/tests/test_stop_loss_live_integration.py` | 5 | Live calls `kite.place_order` with LIMIT; in-flight entry carries reason; `_submit_order` return value propagated |
| Regression smoke | `tests/test_existing_strategies_smoke.py` | 9 (parametrized) | All current templates parse cleanly after the OrderIntent/Fill schema change |
| **Total new tests** | | **36** | |

All 36 new tests pass. Pre-existing `backend/algo/backtest/tests/` suite: 88 pre + 4 new + 1 new = 93 passing. `backend/algo/tests/` paper subset: 52 + 3 new + 2 new = 57 passing. `backend/algo/live/tests/`: 217 + 4 new + 1 new = 222 passing (the 4 pre-existing failures in `test_live_dry_run::TestLiveRuntimeDryFill` x2 and `test_live_postbacks_endpoint` x2 pre-date this branch).

## 3. End-to-end propagation chain (3 runtimes, single observable)

```
Backtest
  Strategy.risk.per_trade.stop_loss_pct
    ─► check_stop_loss_triggers()
      ─► OrderIntent(exit_reason="stop_loss")
        ─► SimBroker.execute() forwards exit_reason
          ─► Fill(exit_reason="stop_loss")
            ─► PositionTracker._apply_sell stamps closed Position
              ─► Position.exit_reason="stop_loss"
                ─► algo.events.order_filled.payload.exit_reason="stop_loss"
                  ─► TradeRow.exit_reason="stop_loss" in summary.trade_list

Paper
  Strategy.risk.per_trade.stop_loss_pct
    ─► check_stop_loss_triggers()
      ─► Signal(reason="stop_loss")
        ─► PaperBroker.execute() forwards reason
          ─► Fill(exit_reason="stop_loss")
            ─► PositionTracker._apply_sell stamps closed Position
              ─► Position.exit_reason="stop_loss"
                ─► algo.events.order_filled.payload.exit_reason="stop_loss"

Live
  Strategy.risk.per_trade.stop_loss_pct
    ─► check_stop_loss_triggers()
      ─► Signal(reason="stop_loss")
        ─► _submit_order() routes through existing rails
          ─► in_flight_entry["reason"]="stop_loss"
            ─► Kite postback → order_filled_live.payload.reason="stop_loss"
              (dry-run synthetic fill: same payload via webhooks.py)
```

All three converge on the same observable: `payload.reason="stop_loss"` (paper / live) or `payload.exit_reason="stop_loss"` (backtest) in algo.events. Trade-list / Position-tracker tagging is uniform.

## 4. Strategy-level empirical impact — deferred

The plan originally called for re-running RSI(2) Connors v1 and v4 daily on this branch to capture before/after numbers. Two reasons that re-run is deferred:

1. **`rsi2_connors_daily_v1` template + `scripts/run_rsi2_connors_baseline.py` live on PR #231** (`strategy/rsi2-connors-daily-spec`), which has not landed on `dev` yet. This branch (`framework/backtest-stop-loss-enforcement-spec`) is the framework fix only — adding strategy templates here would mix concerns.

2. **v4 daily numbers without ex-DIACABS scoping aren't directly comparable** to the pre-fix baseline (53.6% win rate from Sprint 11 PR #228, which was a different universe slice). A clean before/after comparison needs a fixed universe + date range + the strategy template, all of which sit in their respective strategy PRs.

### Re-run sequencing (the plan after this PR lands)

After this PR squash-merges to `dev`:

1. Merge PR #231 (RSI(2) Connors v1) to `dev`. Re-run `scripts/run_rsi2_connors_baseline.py --exclude DIACABS.NS --tag stop3-postfix`. Headline question: does G4 (max DD) come under -15% with stop_loss_pct=3.0 actively enforced?
   - **Before fix (this branch HEAD~10)**: G4 = -19.89%, blocking paper promotion.
   - **After fix (this branch HEAD)**: stop-loss now fires at -3% close-on-close; expected G4 improvement is meaningful but not yet measured.

2. Re-run v4 daily on the same universe + period as Sprint 11 PR #228 (53.6% baseline). With `stop_loss_pct=4.0` now enforced: trade count should rise (stops add exits), max DD should fall, win rate including stops should drop slightly (stops are by construction losing trades) but win rate excluding stops (which is the gate-relevant denominator) should hold steady or rise.

3. MIS v1 (PR #230, `strategy/intraday-mr-long-v1-spec`) — re-run with `stop_loss_pct=2.0` enforced. -18% baseline return becomes diagnosable: if stops cut tail losses, the strategy is dead-from-mean-reversion-failure rather than dead-from-unmanaged-bleed.

Filed as **follow-up tracking artifact** on this PR description (not as a Jira ticket — the empirical work is per-strategy PR work).

## 5. Live trading safety — meaningful gap closed

Until this PR lands, live trading carried **no broker-side stop-loss**. Kite v2 SDK explicitly rejects bracket / SL / SLM / BO / CO orders (`backend/algo/broker/kite_client.py` raises `ValueError`). Strategies declaring `stop_loss_pct > 0` ran live without enforcement.

After this PR: every strategy with `stop_loss_pct > 0` gets local enforcement via the per-bar monitor. The monitor fires at close, submits an aggressive LIMIT SELL via the same rails AST exits use (pre-trade caps, LTP staleness, audit log, postback reconciliation), and tags the order with `reason="stop_loss"` so post-incident analytics can isolate stop-driven exits.

**The bypass on `pre_trade_check`** (kill-switch, max_inr, max_orders, allowed_tickers) is intentional and documented at `runtime.py` near the SL block: stops must fire to bleed risk even when the strategy is otherwise gated. LTP-staleness + position-tracker realism guards remain in force.

## 6. Backwards compatibility

- Strategies with `stop_loss_pct: 0` get NO enforcement (feature disabled).
- Strategies with `stop_loss_pct > 0` (every current template that declares it) get stops in all 3 runtimes.
- `OrderIntent.exit_reason: str = "signal"` default keeps every existing AST-emit call site unchanged.
- `Fill.exit_reason: str = "signal"` default same.
- `Position.exit_reason: str = "signal"` was pre-existing — only the AST-emit `_apply_sell` stamping is new behavior (Task 2 commit `6fceaba`).
- Past `algo.runs` / `algo.events` rows are unaffected.

## 7. Verdict

**Framework fix works as designed across all 3 runtimes.** 36 new tests pin the behavior; propagation verified end-to-end through 3 separate broker layers (SimBroker, PaperBroker, KiteClient). Strategy-level empirical impact (RSI(2) v1 G4 unblock, v4 daily honest DD, MIS v1 tail-control) is the next-PR work, sequenced after this PR squash-merges to `dev`.
