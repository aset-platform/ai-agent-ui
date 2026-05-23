# Strategy Templates — Operator Notes

JSON files in this directory are reference strategy templates loaded
by `parse_strategy()` (`backend/algo/strategy/ast.py`). Each ships
with risk fields declared per the `RiskConfig` schema.

## Stop-loss enforcement (2026-05-23 framework fix)

Strategies with `stop_loss_pct > 0` now have stops enforced in
**backtest, paper, AND live runtimes**. Local enforcement via a
per-bar position monitor (`backend/algo/backtest/stop_loss_monitor.py`);
broker-side bracket orders remain deferred to a future Kite client v3.

- **Backtest**: stop triggers fire at bar close, fills land at the
  NEXT bar's open via `SimBroker` (same fill semantics as
  AST-emitted exits).
- **Paper**: stop triggers fire at bar close, fills land
  immediately at the current LTP via `PaperBroker` (same path as
  AST-emitted exits in paper).
- **Live**: stop triggers fire at bar close, immediate aggressive
  LIMIT SELL submitted to Kite through the existing `_submit_order`
  rails (same pre-trade caps + audit + postback path as AST SELLs).
  LIMIT, not MARKET, per Kite v2 SDK constraints.

All three runtimes record `exit_reason="stop_loss"` on the closed
Position. Triage scripts exclude stop-loss exits from win-rate
denominators.

Strategies with `stop_loss_pct: 0` get no enforcement (feature
disabled). Past backtest runs are unaffected — only future runs
include stops.

### Implementation references

- Pure monitor module: `backend/algo/backtest/stop_loss_monitor.py`
- Backtest integration: `backend/algo/backtest/runner.py`
- Paper integration: `backend/algo/paper/runtime.py`
- Live integration: `backend/algo/live/runtime.py`
- Design spec: `docs/superpowers/specs/2026-05-23-stop-loss-enforcement-design.md`
- Impact report: `docs/research/2026-05-23-stop-loss-enforcement-impact.md`
