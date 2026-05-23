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

---

## F&O 200 universe convention for MIS strategies

For MIS strategies that need to be restricted to liquid F&O underlyings:

- **Backtest**: set `universe.filter.is_fno = true` in the AST. The
  backtest universe resolver intersects with
  `backend/algo/research/intraday_15m_mis_bakeoff/fno_200.csv` automatically.
- **Paper and live**: the AST `is_fno` field is NOT honoured by the paper or
  live runtimes. Operators MUST pre-populate the strategy's
  `caps.allowed_tickers` row (PG, `backend/algo/live/caps_repo.py`) with the
  same F&O list at promotion time. The operator can copy the list out of the
  same CSV:

  ```python
  from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
      load_fno_universe,
  )
  tickers = load_fno_universe()
  # Then pass `allowed_tickers=tickers` to the caps upsert.
  ```

  Failure to do this means the live runtime will accept signals on any
  ticker the strategy emits, including illiquid names — for MIS this
  causes square-off slippage and risks broker rejections.

The F&O list is a static quarterly snapshot. Refresh it when NSE rebalances
the F&O list — see `backend/algo/research/intraday_15m_mis_bakeoff/README.md`.

---

## Universe warmup filter (2026-05-23, ASETPLTFRM-433)

The backtest job pre-filters the universe to tickers whose OHLCV
history covers `period_start - max_warmup_days`. The max warmup is
computed by walking the strategy AST and looking up each
referenced feature's required prior-bar count
(`sma_200` → 200, `rsi_14` → 14, `distance_from_sma5` → 5, etc.).

Without this filter the runner would catch `KeyError: Feature not
in context: X` and silently `continue` on the (ticker, bar) combo —
no entry decision, no exit decision. For long-running positions on
tickers with later-arriving features that would mean the AST exit
branch can't fire either, trapping the position.

After the filter lands, `feature-key-errors=[]` should be near-zero
on every backtest. Residual non-zero counts are usually OHLCV gaps
mid-history (corporate action holidays, delisted-then-relisted
tickers) — track via the runner's tally log line.

Operator notes:

- **Backtest**: filter runs in `backend/algo/backtest/job.py` after
  `resolve_universe`. Logged: `warmup_filter dropped N tickers
  (warmup=K bars, period_start=YYYY-MM-DD)`.
- **Paper / live**: NOT filtered. Paper/live pre-load history per
  ticker via `_bars_by_ticker`; in-process indicator compute
  handles short-history tickers differently (NaN values rather
  than KeyError). Track separately if it becomes an issue.
- **Strategies with `regime_label` / `stress_prob` / `nifty_*`
  only** (no per-ticker indicators) have warmup=0 and pass every
  ticker through.

### Implementation references

- Helper: `backend/algo/strategy/feature_warmup.py`
- Filter: `backend/algo/backtest/universe.py::filter_warmup_eligible`
- Hook: `backend/algo/backtest/job.py`

---

## Mid-trade regime exit (2026-05-23, ASETPLTFRM-435) — RESEARCH OPT-IN ONLY

The `Strategy.mid_trade_regime_check` AST field re-evaluates a
condition tree against per-bar market features and force-closes
ALL open positions with `exit_reason="regime_exit"` when the
condition turns False. Sibling to the entry-time regime gate,
but applied to held positions.

### Default: OFF

The field defaults to `null` in the schema. Production templates
(v1, v2, v3, all `regime_*`, all `bull_momentum_*`, etc.) do NOT
set this field — their backtest / paper / live workflows are
completely unaffected by ASETPLTFRM-435. The runner's per-bar
mid-trade-exit block is gated by `if mtre_check is not None`
which is False for every production template, so the block is
zero-cost when off.

### When to enable

Only for strategy classes where **regime-hostile = thesis broken**:

- Trend-following (regime turns bear → trend's over, exit)
- Breakout (regime turns bear → no breakouts coming, exit)
- Momentum (similar logic)

### When NOT to enable (this is the trap)

Mean-reversion strategies have the OPPOSITE thesis — regime-hostile
days are when the strategy's edge lives. RSI(2) Connors v4 documented
this empirically (CAGR 8.95% → 1.16%, max DD -14.63% → -16.60%,
net return +45.6% → +5.18% — three datapoints, all negative). The
regression test `test_mid_trade_regime_opt_in.py` enforces this:

- A template with the field set MUST include `research` in its
  filename — forcing a deliberate opt-in by the strategy author
- The repo-wide guard refuses to ship any new template that
  silently enables the field

### Reference

The only template with the field set is
`rsi2_connors_daily_v4_research_regime_exit.json`, which exists
solely to demonstrate / reproduce the negative result. v3 stays
the production paper-promotion candidate.

### Implementation

- AST: `Strategy.mid_trade_regime_check: ConditionNode | None`
- Pure module: `backend/algo/backtest/regime_exit_monitor.py`
- Runner integration: `backend/algo/backtest/runner.py` (gated by
  `if mtre_check is not None`)
- Triage: `docs/research/2026-05-23-rsi2-connors-v4-experiments.md`
