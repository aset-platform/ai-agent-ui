# Algo Trading System — v1 architecture overview

End-to-end platform for backtest + paper trading on Indian equities. Live order placement deferred to v2.

## Module layout

```
backend/algo/
├── fees.py                # IndianFeeModel + dated YAML rates
├── fee_rates.yaml         # Two rows: 2020-01-01 → 2026-03-31 (backfill), 2026-04-01 → null
├── instruments/           # Kite /instruments → algo.instruments
│   ├── loader.py          # Derives our_ticker from tradingsymbol + exchange (.NS / .BO)
│   └── repo.py
├── broker/
│   ├── base.py            # BrokerAdapter ABC
│   ├── credentials_repo.py # Fernet-encrypted api_key + access_token in algo.broker_credentials
│   └── kite_client.py     # KiteClient + LiveTickSource adapter
├── strategy/
│   ├── ast.py             # Strategy AST (Pydantic, JSON-schema export)
│   ├── repo.py
│   └── features.py
├── backtest/
│   ├── runner.py          # 1-bulk OHLCV read + indicators + bar walk + RiskEngine + SimBroker
│   ├── data_source.py     # load_ohlcv_window with warmup + _safe_decimal NaN guard
│   ├── indicators.py      # On-the-fly SMA / golden_cross_days_ago (no separate Iceberg table)
│   ├── sim_broker.py      # T+1 OPEN fills (look-ahead-free)
│   ├── positions.py       # Long-only, weighted-avg cost basis, FIFO close
│   ├── evaluator.py       # AST node dispatch
│   ├── event_writer.py    # algo.events Iceberg writer (explicit Arrow schema)
│   ├── runs_repo.py       # algo.runs PG CRUD with summary_json JSONB
│   ├── job.py             # async background worker (BackgroundTasks)
│   └── universe.py        # resolve_universe (scope + filter)
├── paper/
│   ├── runtime.py         # Tick-driven; reuses Evaluator, PositionTracker, indicators
│   ├── broker.py          # PaperBroker (at-tick LTP fills)
│   ├── risk_engine.py     # 3-tier gate (per-trade / daily / portfolio)
│   ├── risk_state_repo.py # algo.risk_state PG CRUD
│   ├── kill_switch_repo.py # PG durability + Redis fast read
│   ├── supervisor.py      # Per-process registry of running PaperRuntime tasks
│   └── replay_rebuilder.py # Restart-replay from algo.events
├── stream/
│   ├── types.py           # Tick + Bar
│   ├── resampler.py       # Pure tick → 1m + 5m bars
│   ├── sources.py         # ReplayTickSource + LiveTickSource
│   ├── service.py         # TickStreamService orchestrator
│   └── bars_writer.py     # algo.intraday_bars Iceberg writer
├── jobs/
│   ├── instrument_refresh.py  # Daily 07:00 IST Kite /instruments pull
│   ├── reauth_notify.py       # Daily 05:30 IST expiring-token email
│   └── risk_state_reset.py    # IST-midnight reset of algo.risk_state
├── routes/                # One router per tab: backtest / broker / fees / instruments / kill_switch / paper / performance / replay / strategies
├── redis_async.py         # Lazy singleton get_async_redis() for kill-switch hot reads
└── tests/

frontend/components/algo-trading/
├── BacktestTab.tsx
├── ConnectBrokerTab.tsx
├── InstrumentsTab.tsx
├── PaperTab.tsx
├── PerformanceTab.tsx
├── ReplayTab.tsx
├── SettingsTab.tsx
├── StrategiesTab.tsx
├── ActiveRunsPanel.tsx          # Paper run lifecycle + fixture dropdown
├── KillSwitchToggle.tsx
├── PaperEventsTimeline.tsx
├── BacktestEquityCurve.tsx      # ECharts
├── BacktestTradeTable.tsx       # Column selector + CSV
└── builder/
    ├── StrategyBuilder.tsx
    ├── StrategyLeversPanel.tsx  # Non-technical edit surface (universe, risk, tunables)
    ├── strategyTunables.ts      # AST walker → editable leaves
    ├── AstTreeView.tsx          # Read-only tree rendering
    ├── JsonPane.tsx             # Read-only JSON + paste-JSON button
    ├── NodePalette.tsx
    └── templates.ts
```

## Storage

| Storage | Used for |
|---|---|
| Postgres `algo.*` | Mutable state: strategies, instruments, broker_credentials, runs, positions, risk_state, kill_switch |
| Iceberg `algo.events` | Append-only event log (canonical event model) |
| Iceberg `algo.intraday_bars` | 1m + 5m resampled bars from live tick stream |
| Redis | `algo:kill:{user_id}` (kill flag, sub-ms reads), broker status |
| MinIO | Reserved for backtest artifact upload (deferred to a future Slice 7c) |

## Flow signatures

**Backtest:**
```
strategy + period + universe
  → load_ohlcv_window(warmup=400d)
  → compute_indicators (SMA + golden_cross_days_ago)
  → for each bar in period_start..period_end:
      → for each ticker:
          → evaluator.eval_node(strategy.root, ctx)
          → _action_to_intent (sets target_weight diff)
          → RiskEngine.gate (3-tier)
          → SimBroker.execute (fills T+1 open)
          → PositionTracker.apply_fill
          → emit signal_rejected / order_filled
      → end-of-day equity snapshot using last_close
  → flush_events to algo.events
  → mark_completed(summary_json) on algo.runs
```

**Paper (replay-fixture mode in v1):**
```
strategy + ReplayTickSource
  → for each tick:
      → resampler.feed(tick)
      → for each closed bar:
          → push to per-ticker history; recompute indicators
          → evaluator.eval_node (try/except KeyError)
          → _action_to_signal
          → RiskEngine.gate
          → PaperBroker.execute (at-tick LTP)
          → PositionTracker.apply_fill
          → emit signal_rejected / order_filled
  → flush_events on shutdown
```

## Key conventions

See `shared/conventions/algo-backtest-fill-semantics` (T+1 open-to-open).
See `shared/architecture/algo-keychain-csi-secrets` (server-side secrets).
See `shared/architecture/algo-strategy-levers-tunables` (UI edit pattern).
See `shared/debugging/algo-bug-catalogue-2026-05-09` for gotchas the v1 build hit.
