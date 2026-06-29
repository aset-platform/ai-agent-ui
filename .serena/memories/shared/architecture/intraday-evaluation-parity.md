# Intraday Evaluation & Backtest/Paper/Live Parity

How strategy **evaluation** (backtest, walkforward, paper) is made a
faithful replica of **live** execution, at the finest resolution each
engine's data source allows. The terse pointers in `.claude/rules/algo.md`
("Intraday execution clock", "Paper/live parity") link here for the full
picture.

## The core idea: signal clock ≠ execution clock

A strategy's **signal cadence** (daily / 15m / 5m / 1m) is decoupled from a
faster **execution clock** that drives exits — ATR trailing, hard / time /
regime stops, and MIS square-off. In live trading the stop is monitored
continuously (broker GTT + WS ticks) regardless of signal cadence; the old
backtest evaluated stops once per signal bar, so a *daily* strategy got one
stop check per day and its ATR trail was effectively meaningless. The
two-clock engine fixes this: signals fire at cadence, exits evaluate on
every execution bar.

## The shared `ExecutionSimulator` (single source of truth)

`backend/algo/backtest/execution_simulator.py` — wraps the live
`TrailingStopManager` and exposes:
`on_buy_fill(ticker, entry_price, atr)`, `drop(ticker)`, `has(ticker)`,
`evaluate_bar(ticker, low, high) -> ExitDecision | None`. It feeds
LOW-then-HIGH per bar (low first for stop-hit, then high to advance HWM),
maps phase→reason (1→`phase1_stop`, 15→`phase1_ratchet`, 2→`trail_stop`),
and returns an `ExitDecision(ticker, exit_reason, trigger_price, phase,
hwm)`.

**Backtest, walkforward, AND paper all drive this one component.** Parity
is *structural*, not coincidental — if live and the simulator share the
state machine, they cannot drift. (Paper previously had its own inline copy
of the LOW/HIGH wrapper; it omitted the HWM-advance-on-high branch — a real
divergence that adopting the shared component fixed.)

## The three engines and their data sources

| Engine | Input | Finest grain | Source |
|---|---|---|---|
| Backtest / walkforward | preserved `stocks.intraday_bars` | **15m** | historical Iceberg (offline) |
| Paper | **live Kite WS ticks** → `Resampler(60)` | **1m** | real-time, generated on the fly |
| Live | broker GTT + WS ticks | tick | real-time |

Each is a replica of live at the best resolution its source allows. They
**cannot be bar-identical** to each other by construction — parity means
the same exit *logic* + fill model + fee model, differing only in bar
resolution.

## Data reality (drives everything)

`stocks.intraday_bars` holds **15m only** (~497 tickers, from ~2022-06).
**No 1m or 5m history exists.** Consequences:
- Backtest/walkforward execution clock = 15m (or daily-fallback).
- **1m/5m-cadence backtests are BLOCKED** (no history) → paper only.
- Paper's 1m is *not* historical — it is resampled live from ticks; that is
  why paper can be finer (1m) than the 15m-limited backtest.

## Resolution selection (backtest/walkforward)

`intraday_coverage(tickers, period_start, period_end)` (one batched query)
returns the finest grain present **per (ticker, window)**. Routing is
per-ticker: covered tickers run on the execution clock; **uncovered tickers
fall back to the daily clock** (flagged in `daily_fallback_tickers`), never
silently stop-less. Coverage is window-scoped, so shorter walkforward folds
legitimately fall back more often than the full-period run. The coverage
probe is wrapped (`except Exception` → daily fallback) so a missing catalog
degrades instead of crashing.

## No Kite in the historical eval path — but paper IS live

Backtest and walkforward make **zero Kite/network calls** — deterministic,
read only preserved Iceberg, gaps degrade/flag rather than live-fetch.
**Paper is the deliberate exception**: it is live-tick-driven, so it uses
the live WS feed by design. `intraday_coverage`/daily-fallback do not apply
to paper.

## Fill model: trigger ± slippage (models the live GTT)

Stop / trailing exits fill **at the trigger price ± directional slippage**,
not at the next-bar open or the bar's last price — matching a live GTT that
fires at its trigger.
- Backtest: `SimBroker._execute_trigger_fill` fills on the CURRENT exec bar
  at the trigger (set `OrderIntent.trigger_price`).
- Paper: `PaperBroker.execute(trigger_price=…)` fills at trigger ±
  `ALGO_PAPER_SLIPPAGE_BPS`.
- Entries and time-stop exits keep the normal market fill (no
  `trigger_price`).

## Fee product must come from the strategy, not the bar grain

`SimBroker` infers fee product from `intent_emitted_ts_ns` (ts set →
INTRADAY). On the two-clock path a daily **CNC** position carries an exec-bar
ts but is a DELIVERY sell — so exit intents MUST set `product` from
`strategy.product` (**CNC→DELIVERY, MIS→INTRADAY**) or the position is
mis-billed cheap intraday STT (optimistic P&L). `PaperBroker` had the
inverse bug (hardcoded DELIVERY → mis-billed MIS); both engines now derive
product from `strategy.product` and are consistent.

## Backward compatibility

A strategy with **trailing disabled** (no `trailing_trigger_pct` /
`trailing_atr_multiplier`) or **no intraday coverage** runs the legacy
daily flat-stop path, byte-identical to before — the execution clock
collapses to the signal clock. Two-clock is the default; there is no
feature flag. Verified by an A/B on one strategy: trailing OFF →
`execution_interval_sec=86400`, flat `stop_loss` exits; trailing ON →
`execution_interval_sec=900`, intraday `phase1_stop`/`phase1_ratchet`/
`trail_stop` exits.

## Result metadata

Each `BacktestSummary` (and walkforward fold) records
`execution_interval_sec` and `daily_fallback_tickers` — so a run is honest
about the resolution that backed it.

## Inspecting paper runs

Paper does **not** create `algo.runs` rows; its fills/signals are written to
the `algo.events` **Iceberg** table, and `_on_bar_close` emits no stdout
summary — so paper activity is invisible in container logs. Inspect via
DuckDB over `algo.events` (columns incl. `ts_date`, `mode`, `type`,
`payload_json`). A trailing exit's `payload_json` carries `trigger_price` +
`trailing_phase` — their presence confirms the shared-simulator path ran.

## Key files

- `backend/algo/backtest/execution_simulator.py` — shared exit component.
- `backend/algo/backtest/coverage.py` — `intraday_coverage`.
- `backend/algo/backtest/sim_broker.py` — `_execute_trigger_fill`, product.
- `backend/algo/backtest/runner.py` — two-clock wiring (daily signal → 15m
  execution; per-ticker routing).
- `backend/algo/paper/runtime.py` — `_evaluate_trailing_exit` (paper path).
- `backend/algo/paper/broker.py` — `PaperBroker` trigger fill + product.
- `backend/algo/backtest/trailing_stop_manager.py` — the state machine.

## Deferred / related

- Transparency UI surfacing `execution_interval_sec` / fallback per run.
- 1m/5m historical backfill (would lift the 15m-only ceiling and unblock
  1m/5m-cadence backtests).
- Live runtime adopting `ExecutionSimulator` directly (today it shares the
  `TrailingStopManager`, not the wrapper).
- Related: `algo-gtt-trailing-stop` (the trailing state machine + live GTT
  phases).
