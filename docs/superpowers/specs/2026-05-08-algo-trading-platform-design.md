# Algo Trading Platform — Design Spec

**Date:** 2026-05-08
**Author:** Abhay Kumar Singh
**Status:** Draft (awaiting user approval)
**Module name:** Algo Trading
**Nav placement:** between Advanced Analytics and Admin
**Predecessor draft:** `algo_trading_platform_spec_v1.md` (this spec supersedes it)

---

## 1. Problem & Goals

The app today gives users insight (Insights, Advanced Analytics,
Recommendation engine) but no way to **act** on it
systematically. The Algo Trading module turns the existing
analytics surface into an **executable strategy lifecycle**:
authoring → backtest → paper trading. v1 stops short of
real-money execution; live trading is reserved for v2 once the
discipline (event log, risk engine, fee model, broker
abstraction) has been proven in paper.

### Goals (v1)

- **Author** strategies as a versioned JSON AST via a visual
  builder; raw-JSON paste for power users; LLM-authored later
  via the existing chat agent (no new agent infra).
- **Backtest** strategies against historical OHLCV (existing
  `stocks.ohlcv`) + future intraday bars (`algo.intraday_bars`),
  with walk-forward CV, slippage, and India-correct fees.
- **Paper trade** the same strategy code against a live Kite
  WebSocket tick stream (read-only; no orders ever sent to
  Zerodha).
- **Replay + audit** any run (backtest or paper) from a single
  append-only event log.
- **Three-tier risk engine** (per-trade · portfolio · daily) +
  user-account kill switch on every signal path.
- **Multi-tenant** for pro + superuser tier; per-user Kite
  credentials encrypted with the existing Fernet pattern.

### Non-goals (v1)

- **No live order placement** — `KiteAdapter.place_order()` is
  intentionally not implemented. The class skeleton is wired so
  v2 can drop in the implementation without restructuring.
- **No F&O** — equity-only universe in v1. The strategy AST
  already accommodates lot-size and option-chain nodes; runtime
  rejects them with a clear error in v1.
- **No multi-broker** — Zerodha only. `BrokerAdapter` ABC keeps
  the door open.
- **No Python-as-strategy SDK** — JSON AST only. Sandbox security
  review is a v2 epic.
- **No headless TOTP-aided Kite login** — daily one-tap login
  link via 05:30 IST email. Anything more aggressive trips
  Zerodha ToS gray areas.

---

## 2. Architecture

### 2.1 Where it lives

```
NAV_ITEMS  →  Portfolio · Dashboard · Advanced Analytics · Algo Trading · Admin
                                                          ▲
                                gated: pro_or_superuser AND page_permissions.algo_trading
```

Single route `/algo-trading` with a top tab strip
(URL-synced `?tab=`). Mirrors §5.4 tabular pattern + Advanced
Analytics precedent — one page, eight tabs, shared toolbar.

### 2.2 Tab strip

| Tab | Purpose | Slice |
|---|---|---|
| `connect` | Connect Zerodha (OAuth, daily re-auth nag) | 2 |
| `instruments` | Browse Kite-derived instrument master | 3 |
| `strategies` | Visual JSON-AST builder + library | 4 + 5 |
| `backtest` | Run/inspect backtests, equity curve, trade list | 7 |
| `paper` | Live paper-trading dashboard | 8 |
| `performance` | Cross-strategy comparison + cohort returns | 9 |
| `replay` | Event-log timeline replay | 10 |
| `settings` | Risk caps, fee-version pinning, kill switch | 0 + 1 |

### 2.3 Module placement (modular monolith)

```
backend/algo/
├── fees.py                # IndianFeeModel + dated YAML rates
├── instruments.py         # Kite /instruments → algo.instruments
├── broker/
│   ├── base.py            # BrokerAdapter ABC
│   ├── sim.py             # SimBroker (backtest + paper fills)
│   └── kite.py            # KiteAdapter (read-only ticker; v1)
├── strategy/
│   ├── ast.py             # JSON-AST schema + validators
│   ├── nodes.py           # Condition / action / composite nodes
│   ├── features.py        # Feature dictionary registry
│   └── runtime.py         # AST interpreter (one ABC, two modes)
├── backtest/              # Walk-forward CV harness
│   ├── runner.py
│   ├── slippage.py
│   └── reports.py         # Equity curve PNG + CSV
├── paper/                 # Live paper runtime + risk engine
│   ├── runtime.py
│   ├── risk.py            # 3-tier governance + kill switch
│   └── recovery.py        # Restart-replay from events
├── events.py              # Append-only Iceberg writer + reader
├── routes/                # One router file per tab
└── tests/

frontend/components/algo-trading/
├── ConnectBrokerTab.tsx
├── InstrumentsTab.tsx
├── StrategiesTab.tsx
├── StrategyBuilder.tsx    # Visual JSON-AST editor
├── BacktestTab.tsx
├── PaperTradingTab.tsx
├── PerformanceTab.tsx
├── ReplayTab.tsx
├── SettingsTab.tsx
└── filterCatalogs.ts      # Feature-dictionary mirror (CI gate)
```

### 2.4 Stack additions

- **MinIO** (1 container) — S3-compatible blob store for
  backtest artifacts (equity curve PNG, full trade CSV, per-bar
  position JSONL, run config snapshot). Cloud migration = endpoint
  swap to native S3.
- **Postgres `algo` schema** — 7 tables (see §3.4).
- **Iceberg `algo` namespace** — 2 tables: `algo.events` (append-only
  event log) + `algo.intraday_bars` (1m + 5m resampled bars).

---

## 3. Canonical Event Model

Every meaningful state transition emits a row in `algo.events`.
Live, paper, and backtest write the same shape; one log powers
replay, audit, and analytics.

### 3.1 Event schema

```python
class AlgoEvent(BaseModel):
    event_id: UUID                 # ULID-style for time-ordered uniqueness
    ts_ns: int                     # nanosecond UTC; preserves intra-bar order
    session_id: UUID               # one backtest run / one paper-trading day
    user_id: UUID                  # multi-tenant isolation
    strategy_id: UUID | None       # null for system events
    mode: Literal["backtest", "paper"]   # "live" reserved for v2
    type: EventType
    payload: dict                  # type-specific Pydantic body
```

### 3.2 Event type ladder

| Tier | Type | Producer | Schema highlights |
|---|---|---|---|
| Market | `market_tick` | KiteAdapter | `symbol, ltp, volume` (paper only) |
| Market | `bar_close` | resampler | `symbol, interval, ohlcv` |
| Decision | `signal_generated` | Strategy runtime | `strategy_id, symbol, side, confidence, features_used` |
| Decision | `signal_rejected` | Risk engine | `reason` enum (daily_loss_cap, exposure_cap, position_cap, instrument_blacklist, kill_switch) |
| Order | `order_submitted` | SimBroker | `internal_order_id, symbol, side, qty, order_type, limit_price` |
| Order | `order_filled` | SimBroker | `fills: [{qty, price, fees: FeeBreakdown}]` |
| Order | `order_cancelled` | SimBroker | `reason` |
| Position | `position_opened` / `_closed` | Position tracker | `symbol, qty, avg_price, realised_pnl_inr` |
| Risk | `risk_breach` | Risk engine | `tier, threshold, observed_value` |
| System | `broker_connected` / `_disconnected` | KiteAdapter | `kite_user_id` |
| Backtest | `backtest_run_started` / `_completed` | Backtest harness | `params_hash, period` |

**Discipline:** events are produced, never mutated. Replay = re-read
the persisted log.

### 3.3 Cache key

`cache:algo:events:{user_id}:{session_id}:{event_type|all}` —
`TTL_VOLATILE` (60s); invalidated by writes via the existing
`_CACHE_INVALIDATION_MAP` glob.

### 3.4 Postgres tables (`algo` schema)

```
algo.broker_credentials  (user_id PK, api_key_fernet, access_token_fernet,
                          access_token_expires_at, kite_user_id, last_login_at)
algo.instruments         (instrument_token PK, tradingsymbol, exchange, segment,
                          lot_size, tick_size, our_ticker FK→stocks.stock_master,
                          loaded_at)
algo.strategies          (id PK, user_id, name, ast_json jsonb, mode, status,
                          created_at, updated_at, archived_at,
                          ast_version int)
algo.runs                (id PK, strategy_id FK, user_id FK, mode, status,
                          period_start, period_end, params_hash,
                          artifact_uri, started_at, completed_at)
algo.positions           (id PK, run_id FK, symbol, qty, avg_price,
                          opened_at, closed_at, realised_pnl_inr)
algo.risk_state          (user_id PK, day_date PK,
                          daily_realised_pnl_inr, daily_unrealised_pnl_inr,
                          breaches jsonb, updated_at)
algo.kill_switch         (user_id PK, active bool, set_by, set_at,
                          reason)
```

---

## 4. Strategy AST Grammar

A strategy = a tree of nodes evaluated against a per-bar context.

### 4.1 Node families

| Family | Nodes | Returns |
|---|---|---|
| **Condition** | `compare`, `and`, `or`, `not`, `crossover`, `between`, `regime` | `bool` |
| **Action** | `buy`, `sell`, `exit`, `hold`, `set_target_weight` | order intents |
| **Composite** | `if`, `select_top_n`, `weighted` | dispatch / aggregate |

### 4.2 Concrete example

```json
{
  "id": "strat_uuid",
  "name": "AA Bullish + Quality v1",
  "universe": {
    "type": "scope", "scope": "watchlist",
    "filter": { "ticker_type": ["stock"], "market": "india" }
  },
  "schedule": {
    "type": "bar_close", "interval": "1d", "time": "15:25 IST"
  },
  "rebalance": { "type": "daily", "max_positions": 10 },
  "root": {
    "type": "if",
    "cond": {
      "type": "and",
      "operands": [
        { "type": "compare",
          "left": { "feature": "today_ltp" },
          "op": ">",
          "right": { "feature": "sma_50" } },
        { "type": "compare",
          "left": { "feature": "pscore" },
          "op": ">=",
          "right": { "literal": 7 } },
        { "type": "compare",
          "left": { "feature": "rsi" },
          "op": "<",
          "right": { "literal": 70 } }
      ]
    },
    "then": {
      "type": "select_top_n", "n": 5,
      "rank_by": { "feature": "today_x_vol" },
      "rank_dir": "desc",
      "action": { "type": "set_target_weight", "weight": 0.20 }
    },
    "else": { "type": "exit", "scope": "all_open" }
  },
  "risk": {
    "per_trade": { "stop_loss_pct": 5, "max_qty": 100 },
    "portfolio": {
      "max_exposure_pct": 80,
      "max_concentration_pct": 25
    },
    "daily": { "max_loss_pct": 2, "max_open_positions": 10 }
  }
}
```

### 4.3 Feature dictionary

Pre-computed leaves the AST can reference. Single registry in
`backend/algo/strategy/features.py`; JSON-schema validation
rejects unknown names. Frontend mirror at
`frontend/components/algo-trading/filterCatalogs.ts` with a
**CI sync test** (mirrors the AA filter-bundle pattern).

| Source | Features |
|---|---|
| `stocks.ohlcv` | `today_ltp`, `prev_day_ltp`, `today_vol`, `today_x_vol`, `away_from_52week_high` |
| `backend/advanced_analytics_filters.py` | `golden_cross_days_ago`, `sma_50`, `sma_200`, `rsi`, `today_dpc` |
| `stocks.fundamentals_snapshot` | `pscore`, `debt_to_eq`, `roce`, `sales_growth_3yrs` |
| `stocks.recommendation_runs` | `recommendation_score`, `recommendation_category` |
| `stocks.forecast` | `forecast_30d_pct_change`, `forecast_confidence` |

### 4.4 Visual builder ↔ JSON

- One-to-one mapping: every AST node has a renderer + an inverse
  parser.
- Editor UI: left palette of node types (drag-to-canvas), tree
  view of the strategy, live JSON pane.
- "Validate" button runs the same Pydantic validator the backend
  uses (compiled via JSON-schema export).
- Save: `POST /v1/algo/strategies` → server-side re-validate
  (defence in depth).

---

## 5. Risk Engine

### 5.1 Three-tier governance

The engine sits **between Signal and Order** in every mode. Same
code, three contexts:

```
Signal → RiskEngine.gate(signal, account_state) → {accept, reject, scale}
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
       Per-trade       Portfolio          Daily
   stop_loss_pct    max_exposure       max_loss_pct
     max_qty       max_concentration   max_open_positions
                  (sector / symbol)
```

| Tier | Default | Source of truth |
|---|---|---|
| Per-trade | SL 5%, max_qty 100 | Strategy AST `risk.per_trade` |
| Portfolio | 80% notional, 25% per ticker, 40% per sector | Strategy AST `risk.portfolio` |
| Daily | -2% account loss, 10 open positions | User account; per-strategy override capped by user setting |

### 5.2 Reject behaviour

Every rejection emits a `signal_rejected` event with `reason`
enum. Replay tab surfaces "this signal would have fired but X
cap blocked it" — no silent drops.

### 5.3 Risk-state recovery

`algo.risk_state` holds intra-day rolling P&L per user; reset at
IST midnight via the existing scheduler. On backend restart,
replay the day's `order_filled` events from Iceberg to rebuild —
no scratch state lost.

### 5.4 Kill switch

User-account level "stop everything" button on the Settings tab:

- Sets Redis flag `algo:kill:{user_id}` (mirrored to
  `algo.kill_switch` for restart durability).
- Every strategy runtime checks before emitting; pending signals
  flushed to `signal_rejected` with `reason="kill_switch"`.
- Re-arming requires a confirm dialog + Postgres write.

---

## 6. Indian Fee Model

Without this, backtests lie. Built first (Slice 1), used by
SimBroker, paper runtime, and the Settings preview widget.

### 6.1 Fee components (per leg, per ISIN, per FY)

| Component | Buy | Sell | Notes |
|---|---|---|---|
| STT | 0.1% (delivery), 0.025% (intraday sell only) | same | Statutory; rates change ~yearly |
| Exchange transaction charge | NSE 0.00297%, BSE 0.00375% | same | Per-exchange variant |
| GST | 18% on (brokerage + exchange + SEBI) | same | |
| SEBI fee | 0.0001% | same | |
| Stamp duty | 0.015% (delivery), 0.003% (intraday) | nil | Buy-side only |
| DP charges | nil | ₹13.5 + GST flat per ISIN | Sell delivery only |
| Brokerage | 0 (Zerodha equity delivery) | 0.03% or ₹20 (intraday) | Plan-dependent |

### 6.2 Implementation

```python
# backend/algo/fees.py
class IndianFeeModel:
    def __init__(self, as_of: date):
        # Loads YAML for the relevant FY; rates change Apr 1 each year.
        self.rates = _load_rates_for(as_of)

    def compute(self, trade: Trade) -> FeeBreakdown: ...
```

Rates live in `backend/algo/fee_rates.yaml`, dated:

```yaml
- effective_from: 2026-04-01
  effective_to: null
  stt:
    delivery_buy: 0.001
    delivery_sell: 0.001
    intraday_sell: 0.00025
  ...
```

Fee version tracked on every `order_filled` event in the payload
(`fee_rates_version: "2026-04-01"`) — same backtest re-run after
rate change won't silently drift.

---

## 7. Broker Abstraction

### 7.1 ABC

```python
# backend/algo/broker/base.py
class BrokerAdapter(ABC):
    @abstractmethod
    def place_order(self, intent: OrderIntent) -> OrderId: ...
    @abstractmethod
    def cancel_order(self, order_id: OrderId) -> None: ...
    @abstractmethod
    def get_positions(self) -> list[Position]: ...
    @abstractmethod
    def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]: ...
```

### 7.2 v1 implementations

| Class | `place_order` | `stream_ticks` |
|---|---|---|
| `SimBroker` | Fills via slippage model + IndianFeeModel | Reads from `algo.intraday_bars` (backtest) or in-memory queue (paper) |
| `KiteAdapter` | **Raises `NotImplementedError("Live trading is v2")`** | Subscribes to Kite WebSocket per user; emits `Tick` events into Redis Stream |

### 7.3 Kite OAuth flow (Slice 2)

1. User clicks "Connect Zerodha" on `connect` tab.
2. Backend issues Kite login URL (with our app's API key + redirect).
3. User authenticates on Kite, returns with `request_token`.
4. Backend computes checksum, exchanges for `access_token` + `kite_user_id`.
5. Persists Fernet-encrypted in `algo.broker_credentials`.
6. Emits `broker_connected` event; flips Redis `algo:broker:{user_id}` to
   `connected`.
7. **Daily 05:30 IST scheduler job** scans all `broker_credentials`
   rows with `access_token_expires_at < today + 1h`; emails affected
   users with one-click re-auth link.

### 7.4 Rate limits + reconciliation

- Kite limits: 3 req/s for orders, 10 req/s for quotes — token-bucket
  in `KiteAdapter`.
- Reconciliation loop (paper only in v1, but built generically):
  every 30s, read broker positions, diff against local — log discrepancies
  to `risk_breach` events. v1 paper has no broker positions but the
  loop scaffold is in place for v2.

---

## 8. Data Layer Split

| Storage | Used for | Why |
|---|---|---|
| **Postgres `algo.*`** | Mutable state (strategies, instruments, broker_creds, runs, positions, risk_state, kill_switch) | Transactional; row-level updates; ≤10K rows per table |
| **Iceberg `algo.events`** | Append-only event log | Immutable, partitioned by `mode + date`; replay via DuckDB |
| **Iceberg `algo.intraday_bars`** | 1m + 5m resampled bars | Append-only, partitioned by `ticker + date`; reused across strategies |
| **DuckDB** | Read engine over Iceberg events + bars | Bulk filter + aggregation; powers Performance + Replay tabs |
| **Redis** | `algo:ticks:{user_id}` (tick fan-out), `algo:broker:{user_id}` (status), `algo:kill:{user_id}` (kill flag), live-paper position cache | Already in stack; pub/sub matches existing chat WS pattern |
| **MinIO** | Backtest artifacts (PNG, CSV, JSONL) | Binary blobs unsuitable for PG; S3-compatible for cloud migration |

---

## 9. Per-Session Slice Decomposition

11 self-contained slices, dependency DAG:

```
Slice 0: Foundation + nav scaffold
   ├──► Slice 1: Indian Fee Model (no deps)
   │       └──► Slices 7, 8 (consume FeeModel)
   ├──► Slice 2: Kite OAuth + broker_credentials (gates 3 + 6)
   │       ├──► Slice 3: Instrument Master
   │       └──► Slice 6: Live tick stream
   └──► Slice 4: Strategy AST schema + storage
           └──► Slice 5: Visual builder UI
                   └──► Slice 7: Backtest engine
                           └──► Slice 8: Paper-trading runtime
                                   └──► Slice 9: Performance + analytics
                                           └──► Slice 10: Replay UI
```

### 9.1 Slice manifest

| # | Slice | Ships | Tab(s) | Est. SP |
|---|---|---|---|---|
| **0** | Foundation + nav | `algo` PG schema migration; `algo.events` Iceberg; nav entry; empty `/algo-trading` page with 8 placeholder tabs; cache invalidation map | Settings (placeholder) | 5 |
| **1** | Indian Fee Model | `IndianFeeModel` + dated YAML; 30+ pytest cases; Settings preview widget | Settings (additive) | 5 |
| **2** | Kite OAuth + creds | `algo.broker_credentials`; OAuth flow; 05:30 IST re-auth job; daily-token-rotation Redis gate | `connect` | 8 |
| **3** | Instrument Master | `algo.instruments` daily Kite refresh; read-only API; column-selector table | `instruments` | 5 |
| **4** | Strategy AST + storage | AST Pydantic schema + JSON-schema export; feature registry; CRUD API; AST validation tests | `strategies` (list) | 8 |
| **5** | Visual builder | `<StrategyBuilder />` drag-tree + JSON pane; validation chip; sample templates | `strategies` (builder) | 8 |
| **6** | Tick stream + bar resampler | KiteAdapter WS (per-user, multiplexed); resampler service; `algo.intraday_bars` Iceberg | hidden under `connect` | 8 |
| **7** | Backtest engine | Walk-forward harness; AST runtime; SimBroker; report generator; MinIO artifacts; equity-curve UI | `backtest` | 13 |
| **8** | Paper-trading runtime | Live runtime; 3-tier risk engine + kill switch; restart-replay recovery; per-strategy dashboard | `paper` | 13 |
| **9** | Performance + analytics | Cohort comparison; strategy-vs-strategy diff; runs table | `performance` | 5 |
| **10** | Replay UI + audit | Event-log timeline scrubber; jump-to-event-type | `replay` | 5 |

**Total: 83 SP across ~5–7 sessions.**

### 9.2 Critical path

- Slice 1 (FeeModel) is fully decoupled — lowest-risk start.
- Slices 0 + 1 + 4 can run in parallel (no shared files).
- Slice 7 is the single largest (13 SP); split into 7a (engine
  headless) + 7b (UI report) if it grows during planning.
- Slice 6 (Kite WS multiplexing) is the trickiest; plan a
  fixture-replay test mode.
- Sensible mid-point demo: ship 0–7 (full backtest pipeline),
  collect feedback, then 8–10.

### 9.3 Suggested session ordering

| Session | Slices | Why |
|---|---|---|
| 1 | 0 + 1 (parallel) | Foundation + decoupled FeeModel — fast confidence |
| 2 | 4 + 5 | Strategy storage + builder — UX backbone, demo-able |
| 3 | 2 + 3 | Broker connectivity + instruments |
| 4 | 7 (backtest) | Largest single slice; finish in one focused session |
| 5 | 6 (tick stream) | Standalone-ish; could fold into session 3 |
| 6 | 8 (paper) | Integration milestone |
| 7 | 9 + 10 (parallel) | Performance + replay — read-only consumers |

---

## 10. Testing Strategy

### 10.1 Per slice

| Slice | Test layers |
|---|---|
| 0 | Migration smoke; nav entry visible for pro/superuser, hidden for general |
| 1 | 30+ pytest fee cases pinned to published Zerodha calculator |
| 2 | OAuth flow integration (mocked Kite); Fernet round-trip; re-auth job |
| 3 | Daily refresh idempotency; ticker-link correctness |
| 4 | AST schema validation (200+ shape cases); CRUD API; JSON-schema export equals frontend mirror (CI sync test) |
| 5 | Vitest builder render + JSON parity; node toggle/drag |
| 6 | Tick → bar resampler unit; replay-from-fixture mode for CI |
| 7 | Walk-forward CV; slippage matches reference; equity curve idempotent given same params_hash |
| 8 | Risk engine 3-tier coverage; kill switch flushes pending; restart-replay recovery |
| 9 | Cohort query correctness over event log |
| 10 | Replay timeline pagination; event-type filter |

### 10.2 E2E (Playwright)

- Pro user can author a JSON-AST strategy, kick off backtest,
  see equity curve.
- Superuser can connect Zerodha (mocked OAuth), see "broker
  connected" banner, disconnect.
- Kill switch from Settings flushes any active paper-trading
  signals.

### 10.3 Lighthouse

- `/algo-trading` route under `/analytics/*` budget (LCP ≤ 3.0s,
  CLS ≤ 0.1).
- Heavy ECharts (equity curve, performance comparison) loaded
  via `next/dynamic({ ssr: false })`.

---

## 11. Rollout

1. Branch `feature/algo-trading-platform-spec` (this commit).
2. Per-slice feature branches off `dev`: `feature/algo-trading-slice-N-<topic>`.
3. Squash-merge to `dev` per CLAUDE.md §4.4 #26.
4. Mid-point demo after Slice 7 lands.
5. Final merge after Slice 10.
6. Promote `dev → qa → release → main` per existing flow.
7. Lighthouse on `/algo-trading` before each promotion.

---

## 12. Open Questions

None at design time. All addressed during brainstorm:

| Topic | Resolution |
|---|---|
| v1 lifecycle scope | Backtest + paper only; no live orders |
| Strategy authoring | JSON AST + visual builder; Python SDK is v2 |
| Access tier | Pro + superuser via page_permissions |
| Data feed | Kite read-only WebSocket (per-user OAuth) |
| MinIO | In v1 for backtest artifacts |
| Deployment | Phase 1 (local Docker Compose) only |
| F&O | v2 |
| Multi-broker | v2 (BrokerAdapter ABC ready) |
| Headless OAuth | Out — daily one-tap link via 05:30 IST email |

---

## 13. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Backtest fakes alpha via look-ahead | M | DuckDB views constrained to `WHERE date < T`; ban joins that could pull future-dated rows; 5+ pytest cases for look-ahead detection |
| Fee rates drift Apr 1 → silent backtest invalidation | M | Fee version stamped on every `order_filled` event; CI test fails if YAML changes without spec update |
| Kite token rotation breaks paper at 06:00 IST | M | Pre-emptive 05:30 IST email + UI banner from 30min before expiry; runtime gracefully pauses strategies on expiry rather than crashing |
| MinIO data lost on local volume reset | L | Document `~/.ai-agent-ui/minio` as a critical user-data path; backup script |
| Multi-tenant credential leak | M | All Fernet-encrypted; per-user PG row isolation; audit-log SQL never trusts `user_id` from JWT alone (re-fetch from DB) |
| Strategy AST as code-injection vector | L | AST is a closed grammar; no Python expression execution; feature names allowlist-validated; literals coerced to typed primitives only |
| Kite WebSocket connection storm (N users × open WS) | M | Connection pool with backoff; per-user WS only when paper is active; reconnect-on-bar-close not on tick |
| 13 SP slice (Backtest engine) overflows session | M | Pre-split into 7a (engine headless + tests) + 7b (UI report tab) ready to invoke |

---

## 14. Future Work (v2+)

- Live order placement via `KiteAdapter.place_order`.
- F&O option chain + peak-margin sim.
- Multi-broker (Upstox, Angel One) via additional adapters.
- Python-strategy SDK with sandboxed runtime.
- LLM-authored strategies via the existing chat agent
  (round-trip: chat → JSON AST → save → backtest).
- Headless or two-step OAuth automation if Zerodha publishes a
  refresh token API.
- DigitalOcean/AWS deployment (Phase 2/3 of original draft).
- Cross-strategy capital allocator (sums target weights → broker
  orders, applies risk caps, net-offsets opposing intents to save
  STT).

---

## Appendix A — Cross-references

| Pattern | CLAUDE.md section |
|---|---|
| Tabular page (column selector + CSV export + chip strip + URL params) | §5.4 |
| Stale-data transparency chip | §5.5 |
| Modal stacking (kill-switch confirm) | §5.6 |
| Scope-aware (`?scope=self|all`) | §5.7 |
| Redis caching strategy | §5.13 |
| E2E conventions (Playwright + testid registry) | §5.14 |
| Performance budgets | §5.15 |

| Module reused | Purpose |
|---|---|
| `auth/encryption.py` (Fernet) | Encrypt Kite api_key + access_token |
| `cache.py` (`_CACHE_INVALIDATION_MAP`) | Bust algo caches on event writes |
| `audit_persistence.py` | Pattern reference for append-only event log |
| `agents/` graph | Future LLM-authored strategy round-trip |
| `stocks.ohlcv` Iceberg | Backtest historical data |
| `backend/advanced_analytics_filters.py` | Feature dictionary leaves (rsi, sma_50, etc.) |
| `tests/backend/test_filter_catalog_sync.py` | Pattern for backend↔frontend feature-registry CI gate |
