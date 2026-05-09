# Algo Trading — Overview

End-to-end platform for **backtest + paper trading** on Indian
equities. Live order placement is deferred to v2 — every code
path that would touch a real broker is gated.

## What v1 ships

| Capability | Where it lives |
|---|---|
| Connect Zerodha (OAuth) | `connect` tab |
| Browse Kite instrument master | `instruments` tab |
| Author strategies (visual builder + JSON pane + Levers) | `strategies` tab |
| Run backtests with risk gating | `backtest` tab |
| Run paper trading against replay fixtures (live WS in v2) | `paper` tab |
| Cross-strategy performance aggregate | `performance` tab |
| Cross-mode event-log replay with filters | `replay` tab |
| Kill switch + (future) risk caps | `settings` tab |

Access is gated `pro_or_superuser` via the existing role system.
The nav entry sits between **Advanced Analytics** and **Admin**.

## Module map

```
backend/algo/
├── fees.py + fee_rates.yaml      # IndianFeeModel (dated YAML)
├── instruments/                   # Kite /instruments → algo.instruments
├── broker/                        # Kite OAuth + KiteClient + LiveTickSource
├── strategy/                      # AST schema + repo + features
├── backtest/                      # runner, indicators, sim_broker, evaluator
├── paper/                         # runtime, broker, risk_engine,
│                                  # supervisor, replay_rebuilder
├── stream/                        # tick → bar resampler + sources
├── jobs/                          # scheduled jobs (instruments refresh,
│                                  # reauth notify, risk_state reset)
├── routes/                        # one router per tab
└── tests/
```

```
frontend/components/algo-trading/
├── BacktestTab.tsx + BacktestEquityCurve / TradeTable / SummaryCards / RunForm
├── ConnectBrokerTab.tsx
├── InstrumentsTab.tsx
├── PaperTab.tsx + ActiveRunsPanel + PaperEventsTimeline
├── PerformanceTab.tsx
├── ReplayTab.tsx
├── SettingsTab.tsx + KillSwitchToggle
├── StrategiesTab.tsx
└── builder/
    ├── StrategyBuilder.tsx
    ├── StrategyLeversPanel.tsx + strategyTunables.ts
    ├── AstTreeView.tsx           # read-only tree
    ├── JsonPane.tsx              # read-only JSON + paste-JSON button
    ├── NodePalette.tsx
    └── templates.ts
```

## Storage layout

| Storage | Used for |
|---|---|
| **Postgres `algo.*`** | Mutable state — strategies, instruments, broker_credentials, runs, positions, risk_state, kill_switch |
| **Iceberg `algo.events`** | Append-only event log — canonical event model across backtest + paper |
| **Iceberg `algo.intraday_bars`** | 1m + 5m resampled bars from the live tick stream |
| **Redis** | `algo:kill:{user_id}` — kill flag for sub-ms reads on the runtime hot path |
| **MinIO** | Reserved for backtest artifact upload (deferred) |

## Backtest flow

```
strategy (AST) + period + universe
  ↓
load_ohlcv_window  (220-day warmup → SMA200 well-formed at period_start)
  ↓
compute_indicators (SMA + golden_cross_days_ago, on-the-fly)
  ↓
for each bar in period_start..period_end:
    for each ticker:
      evaluator.eval_node(strategy.root, ctx)   ← reads SMAs etc.
      _action_to_intent (set_target_weight diff)
      RiskEngine.gate (3-tier: per-trade / daily / portfolio)
      SimBroker.execute → fills at T+1 OPEN (no look-ahead)
      PositionTracker.apply_fill
      emit signal_rejected | order_filled
    end-of-day equity snapshot using last_close
  ↓
flush_events to algo.events (single Iceberg commit)
mark_completed(summary_json) on algo.runs
```

See [Backtest engine](backtest.md) for the full sequence.

## Paper trading flow (replay-fixture mode in v1)

```
strategy + ReplayTickSource (.jsonl fixture)
  ↓
for each tick:
    resampler.feed(tick)
    for each closed bar:
      push to per-ticker history; recompute indicators
      evaluator.eval_node (try/except KeyError)
      _action_to_signal
      RiskEngine.gate
      PaperBroker.execute → fills at-tick LTP
      PositionTracker.apply_fill
      emit signal_rejected | order_filled
  ↓
flush_events on shutdown
```

See [Paper trading](paper-trading.md) for the runtime details
and the fixture-generation recipe.

## v2 deferrals

These are **explicitly out of v1 scope** per the epic spec § 12:

- Live order placement (Kite or any broker)
- Live Kite WebSocket multiplexer (one WS per user → fan out to many strategies)
- Reconciliation loop (paper position diff vs broker)
- MinIO artifact upload for backtest runs
- Walk-forward CV harness
- F&O instruments
- Multi-broker support (BrokerAdapter ABC ready)

## Where to look next

- [Backtest engine](backtest.md) — fill semantics, risk gating, indicator computation
- [Paper trading](paper-trading.md) — runtime, supervisor, fixtures
- [Strategies](strategies.md) — AST grammar, visual builder, levers panel
- [Secrets management](secrets.md) — Keychain → docker-compose CSI-style pattern
- [Troubleshooting](troubleshooting.md) — bug catalogue from the v1 build
