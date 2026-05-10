# Algo Trading v2 — Live-Trading-Readiness Design Spec

**Date:** 2026-05-09
**Author:** Abhay Kumar Singh
**Status:** Draft (awaiting user approval)
**Module name:** Algo Trading (existing)
**Predecessor specs:**
- `algo_trading_platform_spec_v1.md` (original aspirational spec, 354 lines)
- `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (v1 implementation spec, 638 lines)

**Working branch (post-merge):** `feature/algo-trading-v2-integration` (cut after `feature/algo-trading-v1-integration` is merged into `dev` — already done at PR #141 → squash `27e98730`).

---

## 1. Problem & Goals

v1 shipped backtest + paper trading with full risk gating, kill switch, indicator engine, T+1 fill semantics, and a Kite OAuth flow that is **read-only by construction** (`KiteAdapter.place_order` raises `NotImplementedError`). The user has now run real backtests + paper runs against real Indian stocks and validated the discipline holds.

v2 turns the same engine into a **single-user, single-strategy live trader on Zerodha Kite**, with safety belts that make it materially harder to lose money to a bug than to lose money to a bad strategy.

### Goals (v2)

- **Promote `BYO_SECRET_KEY`** (Fernet master key for at-rest credential encryption) from `.env` plaintext to the same Keychain → docker-compose `secrets:` mount → `load_secret()` flow that already backs `algo_kite_api_secret`. No new code path; just close the last secret-in-`.env` hole before live trading.
- **Auto-wire restart-replay rebuilder** so paper runs survive backend restarts cleanly. Helper exists in `backend/algo/paper/replay_rebuilder.py` from v1; just needs to be called from app startup.
- **Live Kite WebSocket multiplexer** — one persistent WS per user, fan out to many strategies, durable reconnect with gap-fill from Kite's historical 1m API. Replaces today's replay-fixture-only `LiveTickSource`.
- **Walk-forward CV harness** — split a backtest period into rolling train/test windows; aggregate per-window metrics. Gate for "should this strategy go live."
- **Reconciliation loop** — periodic Kite `get_positions()` ↔ `algo.positions` diff. Surfaces drift as `position_drift_detected` events. v2 is **alert-only** — never silently overwrites.
- **Live order placement on Kite** — final slice. Default-off per user, ₹0 cap, kill-switch armed. User opts in by setting a per-strategy ₹ cap and disarming. Risk engine output is now binding instead of advisory.

### Non-goals (v2)

- **No F&O** — equity-only universe still. Same v1 grammar; runtime still rejects `option_chain` nodes.
- **No multi-broker** — Kite only. Adapter ABC still parked.
- **No Python-as-strategy SDK** — JSON AST only.
- **No MinIO artifact upload** — backtest artifacts continue living in `algo.runs.summary_json` (JSONB). Big-result archival is a v3 nice-to-have.
- **No headless TOTP / refresh-token automation** — daily one-tap link via existing 05:30 IST email job.
- **No cross-strategy capital allocator** — single strategy per user goes live first; allocator is v3.
- **No new chat-agent strategy authoring** — v1 ships JSON pane + Levers panel; that's enough to author for live.
- **No order-type expansion** — only `MARKET` and `LIMIT` go live in v2. `SL`, `SLM`, `BO`, `CO` deferred.

---

## 2. Architecture

### 2.1 What changes (and what doesn't)

v1's modular monolith stays intact. v2 adds 4 new modules and 1 new tab; everything else is reused.

```
backend/algo/
├── fees.py + fee_rates.yaml      # unchanged
├── instruments/                   # unchanged
├── broker/
│   ├── base.py                   # unchanged ABC
│   ├── sim.py                    # unchanged
│   ├── kite.py                   # ★ place_order, cancel_order, modify_order
│   │                             #   gate by user.live_orders_enabled
│   └── ws_multiplexer.py         # ★ NEW: per-user persistent WS,
│                                 #   fan-out to per-strategy queues,
│                                 #   gap-fill via 1m historical
├── strategy/                      # unchanged
├── backtest/
│   ├── runner.py                 # unchanged
│   ├── walkforward.py            # ★ NEW: train/test window iterator
│   └── ...
├── paper/                         # mostly unchanged; runtime gains
│                                  # source="live-ws" mode
├── live/                          # ★ NEW (parallel to paper/)
│   ├── runtime.py                # mirrors paper.runtime; orders go to KiteAdapter
│   ├── safety.py                 # pre_trade_check + dollar/qty caps
│   ├── reconciliation.py         # broker positions ↔ algo.positions diff
│   └── tests/
├── jobs/
│   ├── algo_reconciliation.py    # ★ NEW: every N min during market hours
│   └── ...
├── routes/
│   ├── live.py                   # ★ NEW: live mode start/stop, caps CRUD
│   └── ...
└── tests/

frontend/components/algo-trading/
├── ...
├── LiveTab.tsx                   # ★ NEW (or fold into PaperTab as a mode toggle —
│                                 #   see §2.4)
├── LiveSafetyBeltsForm.tsx       # ★ NEW: per-strategy cap form
├── ReconciliationDriftPanel.tsx  # ★ NEW
└── builder/                       # unchanged
```

### 2.2 New tab placement

Two reasonable layouts; spec recommends **(B)**:

| Option | Pros | Cons |
|---|---|---|
| (A) New `live` tab between `paper` and `performance` | Cleanest visual separation; obvious risk demarcation | Duplicate dashboards (paper + live) for largely the same UX; user has to mental-merge two timelines |
| **(B) Mode toggle on `paper` tab → "Paper" / "Live" segment control** | Single dashboard, single timeline, clear chip when in Live mode | Risk: a fat-finger flip into Live; mitigated by 2-step confirm + kill-switch armed default |

(B) is the recommended path. The mode toggle is gated by:
1. Kite connected.
2. User-set per-strategy live cap > 0.
3. Kill switch DISARMED (separate state from cap).
4. Last walk-forward CV report on this strategy newer than 30 days AND showing positive aggregate win-rate.

Failing any of these flips the toggle disabled with a tooltip explaining which gate is closed.

### 2.3 Stack additions

- **No new containers.** WS multiplexer runs in-process inside the backend container — single-user platform, no fan-out concerns at infra scale.
- **No new Postgres schemas.** v2 adds 3 columns + 1 table to the existing `algo.*` schema.
- **No new Iceberg tables.** v2 adds new event types to `algo.events`.

### 2.4 Stack delta

```
algo.live_caps              (NEW: user_id PK, strategy_id PK, max_inr, max_orders_per_day,
                             allowed_tickers jsonb, live_orders_enabled bool,
                             approved_by, approved_at, last_walkforward_run_id FK)

algo.runs                   (+columns: parent_walkforward_id UUID,
                             window_start date, window_end date)

algo.positions              (+column: source enum('paper','live'))

algo.events                 (+new types: position_drift_detected, drift_resolved,
                             order_submitted_live, order_acknowledged_live,
                             order_filled_live, order_rejected_live,
                             order_cancelled_live, ws_connected, ws_disconnected,
                             ws_gap_filled)
```

---

## 3. Canonical Event Model — v2 additions

v1's `AlgoEvent` shape unchanged. New `mode = "live"` literal joins `"backtest"` and `"paper"`. New event types:

| Tier | Type | Producer | Schema highlights |
|---|---|---|---|
| WS | `ws_connected` | KiteWsMultiplexer | `kite_user_id, subscriptions: [str]` |
| WS | `ws_disconnected` | KiteWsMultiplexer | `reason, retry_count` |
| WS | `ws_gap_filled` | KiteWsMultiplexer | `interval, missing_seconds, ticks_replayed` |
| Order (Live) | `order_submitted_live` | KiteAdapter | `internal_order_id, kite_order_id, symbol, side, qty, order_type, limit_price` |
| Order (Live) | `order_acknowledged_live` | KiteAdapter | `kite_order_id, exchange_order_id, status` |
| Order (Live) | `order_filled_live` | KiteAdapter | `kite_order_id, fills, fees, fee_rates_version` |
| Order (Live) | `order_rejected_live` | KiteAdapter | `kite_order_id, rejection_reason` |
| Order (Live) | `order_cancelled_live` | KiteAdapter | `kite_order_id, reason` |
| Reconciliation | `position_drift_detected` | reconciliation job | `tier (qty/avg/realised), our: {...}, broker: {...}, diff` |
| Reconciliation | `drift_resolved` | reconciliation job | `previous_event_id, resolution: 'auto-broker-wins' \| 'manual'` |
| Walk-forward | `walkforward_window_started/_completed` | walkforward harness | `window_index, train_period, test_period, summary` |

**Discipline holds** — these are all immutable, append-only, type-tagged. No new persistence pattern.

---

## 4. Strategy AST Grammar

**Unchanged.** v1 grammar handles every shape needed for the Golden Cross v1 strategy and any single-rule SMA/RSI/Piotroski derivative the user has expressed interest in. Live mode reads the same AST.

The Levers panel auto-discovery (`walkTunables`) likewise unchanged. The only addition is one new top-level lever:

| Group | Field | Control | Why |
|---|---|---|---|
| Live (only visible when live_orders_enabled) | `max_inr` | number, ₹100–₹1,00,000 | Per-strategy ₹ cap |
| Live | `max_orders_per_day` | number, 1–50 | Hard upper on ordering velocity |
| Live | `allowed_tickers` | multi-chip from instrument master | Allow-list (NOT block-list) |

These are stored in `algo.live_caps`, NOT inside the strategy AST — they're per-(user, strategy) deployment config, not strategy logic. Keeping them out of the AST means the same strategy can be paper-traded by user A and live-traded by user B with different caps without forking the AST.

---

## 5. Risk Engine — promotion to binding

v1's `RiskEngine.gate(signal, account, risk, last_price)` is unchanged in shape. What changes is enforcement:

| Tier | v1 (paper) | v2 (live) |
|---|---|---|
| Kill switch | Drops signal | Drops signal **AND** cancels any in-flight unfilled order on Kite |
| Per-trade `max_qty` | Hard reject | Hard reject |
| Daily `max_loss_pct` | Hard reject; `algo.risk_state.day_realised_pnl` updated post-fill | Hard reject **AND** triggers a strategy-level pause until manual disarm |
| Daily `max_open_positions` | Hard reject | Hard reject |
| Portfolio `max_concentration_pct` | Hard reject | Hard reject |
| Portfolio `max_exposure_pct` | Scale-down qty (allowed) | Scale-down qty (allowed) |
| **NEW v2 layer:** `live_caps.max_inr` | n/a | Hard reject if `qty * last_price > max_inr − cumulative_inr_today` |
| **NEW v2 layer:** `live_caps.max_orders_per_day` | n/a | Hard reject if order would exceed |
| **NEW v2 layer:** `live_caps.allowed_tickers` | n/a | Hard reject if symbol not in allow-list |

The new v2 layer runs **before** the v1 tiers. A symbol blocked by `allowed_tickers` is rejected at ₹0 of CPU cost — never reaches portfolio-cap arithmetic.

### 5.1 Kill-switch latency budget

Live-mode signal → kill-check → broker-send chain MUST complete in **< 50ms p99**. Redis read for the kill flag is the tightest budget. Falling back to PG (if Redis is down) bumps p99 to ~500ms — acceptable for a single-user platform but logged as a `WARNING`-level event so we know the cache layer was down during a live session.

### 5.2 Order in-flight cancellation on kill

When kill switch flips ARMED while orders are submitted-but-not-filled, the runtime MUST:

1. Stop emitting new signals (already happens in v1).
2. Iterate `algo.runs.live_orders_in_flight` and call `KiteAdapter.cancel_order` for each.
3. Emit `order_cancelled_live` per cancellation.
4. Surface a banner: "Kill switch armed: N in-flight orders cancelled, M positions held (manual exit required)."

We deliberately do NOT auto-flatten open positions. Auto-flatten is a separate, irreversible action that needs explicit user intent — kill switch is "stop trading", not "exit everything".

---

## 6. Indian Fee Model

**Unchanged.** v1's `fee_rates.yaml` already has the 2020-01-01 → 2026-03-31 backfill row + 2026-04-01 → null current row. Live fills land at the same code path; `fee_rates_version` stamping is identical.

One v2 addition: live fills get an extra `kite_brokerage_inr` field on `FeeBreakdown` (Kite charges ₹0 for delivery on equity, but the schema reserves the slot for derivatives later).

---

## 7. Broker Abstraction

### 7.1 KiteAdapter implementation finally lands

`KiteAdapter` v1 has 3 stubs returning `NotImplementedError`:

```python
def place_order(self, ...):  raise NotImplementedError
def cancel_order(self, ...): raise NotImplementedError
def modify_order(self, ...): raise NotImplementedError
```

v2 implements all three using Zerodha's published Kite Connect REST API. Order types limited to `MARKET` and `LIMIT`; product `CNC` (delivery) only — no MIS / NRML in v2. Variety `regular` only.

### 7.2 WebSocket multiplexer

```python
class KiteWsMultiplexer:
    """One persistent WS per (user_id), fan out to per-strategy queues.

    - Single connection authenticated via the user's Kite access_token.
    - Subscribes/unsubscribes to instrument tokens as strategies start/stop.
    - On disconnect: exponential backoff reconnect; on reconnect, pull
      missed period from Kite historical 1m API and replay through the
      per-strategy resampler (emits ws_gap_filled).
    - Health: emits ws_connected / ws_disconnected events.
    - Backpressure: per-strategy queue is bounded; on overflow, drop
      oldest tick and emit a tick_dropped event (v3-only event type;
      v2 logs WARNING).
    """
```

### 7.3 Reconciliation

Runs every **5 minutes** during market hours (09:15 – 15:30 IST), driven by the existing scheduler (`scheduler.py`). For each user with at least one live strategy active today:

1. Fetch broker positions via `kite.positions["net"]`.
2. Fetch our positions via `algo.positions WHERE source='live' AND closed_at IS NULL`.
3. Compute diff per (symbol, qty, avg_price, realised_pnl).
4. For each non-zero diff → emit `position_drift_detected`.
5. v2 is **alert-only** — never silently writes broker values back.
6. Cleared diffs (next run shows 0) → emit `drift_resolved`.

If drift persists for > 3 consecutive runs, the user gets an email + a red banner on the Live tab forces manual reconciliation before further orders can be sent. (Same gate the live mode toggle uses — we just flip it shut.)

---

## 8. Data Layer Split

**No changes to v1's split.** PG still owns mutable state; Iceberg `algo.events` is still the canonical event log; DuckDB still backs analytics reads. The v2 additions sit inside the existing tables/columns described in §2.4.

---

## 9. Per-Slice Decomposition

6 self-contained slices, dependency DAG:

```
Slice V2-0: Foundation (Keychain BYO_SECRET_KEY + replay-rebuilder wire-in)
   ├──► Slice V2-1: Live Kite WS multiplexer (gates V2-3 + V2-5)
   │       ├──► Slice V2-3: Reconciliation loop
   │       └──► Slice V2-5: Live order placement
   └──► Slice V2-2: Walk-forward CV harness (no runtime deps)
           └──► Slice V2-5 (gates the live-mode toggle)
```

(`Slice V2-4: Live safety belts` ships as a sub-section of V2-5 — too small as a standalone session.)

### 9.1 Slice manifest

| # | Slice | Ships | Tab(s) | Est. SP |
|---|---|---|---|---|
| **V2-0** | Foundation | Keychain `byo_secret_key` slug; `load_secret()` for Fernet master in `auth/encryption.py`; backend startup invokes `replay_rebuilder.rebuild_all()` (idempotent); 5 pytest cases for both | none (config-only) | 3 |
| **V2-1** | Kite WS multiplexer | `backend/algo/broker/ws_multiplexer.py`; per-user singleton; subscribe/unsubscribe by instrument_token; gap-fill via Kite historical 1m; `paper.runtime` gains `source="live-ws"` mode; new `ws_*` events; replay-from-fixture mode for CI | `paper` (source dropdown) | 13 |
| **V2-2** | Walk-forward CV | `backend/algo/backtest/walkforward.py` train/test window iterator; per-window summary aggregation; new `algo.runs.parent_walkforward_id`; new sub-tab on `backtest` tab; report renders per-window equity curves stacked | `backtest` (sub-tab) | 8 |
| **V2-3** | Reconciliation | `backend/algo/live/reconciliation.py`; scheduler job every 5 min in market hours; new event types; `<ReconciliationDriftPanel />` on `paper` tab; alert-only | `paper` | 5 |
| **V2-5** | Live order placement (incl V2-4 safety belts) | `KiteAdapter.place_order/cancel_order/modify_order`; `algo.live_caps` table + CRUD; `<LiveSafetyBeltsForm />`; mode toggle on `paper` tab; new live event types; binding risk engine; in-flight order cancellation on kill; live mode toggle gates (Kite connected + caps set + kill disarmed + walk-forward newer than 30 days) | `paper` (mode toggle) | 21 |

**Total: 50 SP across ~4–5 sessions.**

### 9.2 Critical path

- **V2-0 unblocks everything else** but is XS — cleanup that should land first so we don't ship live trading on a `.env`-plaintext Fernet key.
- **V2-1 (WS multiplexer) gates V2-3 + V2-5.** Without live ticks, neither reconciliation nor live orders mean much. This is the trickiest slice technically (reconnect, gap-fill).
- **V2-2 (walk-forward) is fully decoupled** — can run in parallel with V2-1.
- **V2-3 (reconciliation) MUST land before V2-5.** Live orders without reconciliation = silent state drift.
- **V2-5 is the largest single slice (21 SP)** and the riskiest. Default-off, ₹0-cap, kill-armed end-state.

### 9.3 Suggested session ordering

| Session | Slices | Why |
|---|---|---|
| 1 | V2-0 + V2-2 (parallel) | Foundation + decoupled walk-forward harness — fast confidence; both are safe |
| 2 | V2-1 (WS multiplexer) | Standalone large slice; finish in one focused session; ends with paper running on real ticks |
| 3 | V2-3 (reconciliation) | Sets up the safety net before V2-5 |
| 4 | V2-5 (live orders, default-OFF) | Final slice; ships the code but leaves it dormant per user |
| 5 | (Manual) Live ramp | User opts in: ₹0 → ₹1k → ₹10k → ₹50k → user-chosen, watching events at each step |

---

## 10. Testing Strategy

### 10.1 Per slice

| Slice | Test layers |
|---|---|
| V2-0 | Fernet round-trip with key from `/run/secrets/byo_secret_key`; replay-rebuilder idempotent (run twice → second is no-op); existing BYOM keys still decrypt after migration |
| V2-1 | Mocked WS server (existing `pytest-asyncio` fixture); reconnect after kill; gap-fill replays the right number of bars; subscribe/unsubscribe doesn't leak; backpressure drops oldest |
| V2-2 | Walk-forward window iterator covers full period; train/test windows non-overlapping; per-window summary aggregates correctly; SQLite-backed integration test runs a known strategy through 3 windows |
| V2-3 | Synthetic drift fixture (broker says 100 qty, we say 50) → emits `position_drift_detected` once, not on every poll; `drift_resolved` emitted exactly once when broker matches; consecutive-run counter resets correctly |
| V2-5 | KiteAdapter.place_order against mocked Kite REST (`responses` lib); rejection path; cancel during in-flight; risk engine binding (every cap rejects); kill switch cancels all in-flight; `live_caps` enforcement; CI never hits real Kite |

### 10.2 E2E (Playwright)

- User opens `paper` tab, flips mode toggle to "Live" — toggle is disabled with tooltip "Set live cap first" until they fill `<LiveSafetyBeltsForm />`. After form submit, toggle becomes enabled. Confirm 2-step modal blocks accidental flip.
- Kill switch on Live mode: arm → in-flight orders cancelled banner appears; disarm requires retyping strategy name.
- Reconciliation drift panel surfaces a synthetic drift (test seeds it via `algo.events`) and clears when removed.
- Walk-forward report on Backtest tab shows N stacked equity curves for N windows.

### 10.3 Live integration smoke (NEW for v2)

A separate `e2e/live-smoke/` Playwright project, **opt-in via env var** (`RUN_LIVE_SMOKE=1`), runs against a Kite paper account (Zerodha's "test" environment):

1. Connect Kite → see `broker_connected` event.
2. Author a 1-ticker strategy (e.g. `if today_ltp > 0 set_target_weight=0.01`).
3. Set live cap = ₹100, max_orders_per_day = 1.
4. Disarm kill switch.
5. Flip mode toggle to Live.
6. Wait for one tick → verify `order_submitted_live` event lands within 2 minutes.
7. Cancel order, arm kill switch, disconnect.

Lives in CI but skipped by default (rate-limited by Kite, requires real credentials in CI secret). Run manually before any PR-to-`main` promotion that touches `backend/algo/live/`.

### 10.4 Lighthouse

`/algo-trading` route already meets `/analytics/*` budget in v1. v2 additions (mode toggle, drift panel, walk-forward sub-tab) are lazy-loaded; no budget change expected.

---

## 11. Rollout

1. Branch `feature/algo-trading-v2-integration` from `dev` after v1 squash lands (already merged).
2. Per-slice feature branches off the v2 integration branch: `feature/algo-trading-v2-slice-N-<topic>`.
3. Each slice merges to the v2 integration branch via squash.
4. v2 integration branch → `dev` via single PR after V2-5 lands.
5. Squash-merge to `dev` per CLAUDE.md §4.4 #26.
6. **Live ramp is manual**, post-merge, per-user, controlled by `live_caps.live_orders_enabled`. Code ships dormant; the user flips it on themselves.
7. First production live order happens with cap=₹1,000, single-ticker, Golden-Cross-v1 (or any strategy that has a green walk-forward report).
8. Cap doubles per day for 7 days if no `position_drift_detected` and no kill activations during market hours.
9. After 7 clean days, user sets their own cap.

---

## 12. Open Questions

| Topic | Resolution |
|---|---|
| Kite paper environment for CI smoke | Use Kite's `enctoken=test` paper account (no real money); requires its own GitHub Actions secret; opt-in via `RUN_LIVE_SMOKE=1` |
| Auto-flatten on kill | **No.** Kill switch stops new orders + cancels in-flight; held positions stay open. User must exit manually. Spec'd in §5.2. |
| Reconciliation auto-heal | **No.** Alert-only in v2. Auto-heal (overwrite our PG with broker truth) is a v3 question once we have data on what kinds of drift occur in practice. |
| Order-type expansion (SL/SLM/BO/CO) | Deferred. Two reasons: (a) `SL` requires a trigger price, complicates pre-trade-check; (b) `BO`/`CO` are bracket/cover orders that imply a tighter risk-engine integration we should design separately. |
| Multi-strategy live | Deferred. Single live strategy per user in v2. The `live_caps` schema accommodates per-strategy already; the limit is enforced at API layer, not data layer. |
| Headless OAuth / refresh token | Still out per v1. Daily 05:30 IST email link unchanged. |
| Promote `BYO_SECRET_KEY` rotation | v2 ships the migration to Keychain; rotation is a v3 process question (graceful re-encryption of all stored keys). |
| Cross-broker support | v3. ABC remains parked. |

---

## 13. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Bug sends a wildly oversized live order | M | Three independent caps: `max_qty` (per-trade), `max_inr` (per-strategy), `max_orders_per_day` (per-strategy); pre-trade check rejects if any breached; first ramp step is ₹1,000 (max possible loss before user notices = ₹1,000) |
| Kill switch fails to cancel in-flight orders during outage | L | Kill check first reads Redis (sub-ms); on Redis-down, falls back to PG (~5ms); on PG-down, runtime refuses to send new orders entirely (fail-closed); cancellations are best-effort but logged with `order_cancel_failed` event for manual cleanup |
| Position drift goes unnoticed | M | 5-min reconciliation; > 3 persistent runs → email + force-reconcile gate on Live mode toggle; drift events visible on Replay tab |
| WS reconnect storm DoSing Kite | L | Exponential backoff (1s, 2s, 4s, ... cap 60s); per-user singleton means even worst-case = 1 connection per user |
| Walk-forward CV gate is treated as "validation" not "warning" | M | UI explicitly labels: "Past results don't guarantee future performance — use this to compare strategies, not to certify them"; no automated promotion based on walk-forward results |
| Live mode toggle accidentally flipped | L | 2-step confirm modal requiring strategy name retype; toggle disabled until 4 gates pass (Kite connected, caps set, kill disarmed, walk-forward < 30 days) |
| Fernet master key lost during BYO_SECRET_KEY migration | M | V2-0 includes a one-time backup of all `byo_keys.api_key_fernet` rows to a `byo_keys_backup_2026_05_09` table; migration is reversible; rollback procedure documented in slice plan |
| Reconciliation false positives flood events | L | Drift threshold tunable per user (default 0); identical-diff-on-consecutive-runs deduped; only first occurrence emits an event, subsequent stay in `algo.live.drift_state` PG table until cleared |
| Single-user assumption breaks under multi-tenant load | L | v2 spec explicitly single-user-single-strategy live; no marketing claims of multi-tenant live trading; future scale-out is a v3 problem with its own design doc |

---

## 14. Future Work (v3+)

- **F&O option chain support** + peak-margin sim
- **Multi-broker** (Upstox, Angel One) via additional adapters
- **Cross-strategy capital allocator** (sums target weights → broker orders, applies risk caps, net-offsets opposing intents to save STT)
- **MinIO artifact upload** for backtest runs
- **Auto-flatten policy** (with cooling-off + manual-override workflow)
- **Reconciliation auto-heal** (broker-wins + audit trail)
- **Order types beyond MARKET/LIMIT** — SL, SLM, BO, CO
- **Multi-strategy live** for a single user (the schema already accommodates; just lift the API gate)
- **Python-strategy SDK** with sandboxed runtime (Restricted Python? wasmer?)
- **LLM-authored strategies** via the existing chat agent (chat → JSON AST → save → backtest → walk-forward → live)
- **Cloud deployment** (Phase 2/3 of the original v1 spec) — when single-user moves to multi-tenant
- **Headless OAuth** if Zerodha publishes a refresh-token API

---

## Appendix A — Cross-references

| Pattern | CLAUDE.md section |
|---|---|
| Keychain → CSI secret pattern | feedback memory `feedback_keychain_csi_secret_pattern` |
| Strategy Levers panel auto-discovery | feedback memory `feedback_strategy_levers_pattern` |
| T+1 backtest fill semantics (locked) | feedback memory `feedback_backtest_t1_open_semantics` |
| Tabular page (column selector + CSV) | §5.4 |
| Modal stacking (kill-switch confirm, mode toggle confirm) | §5.6 |
| Redis caching strategy (kill-switch hot path) | §5.13 |
| E2E conventions (Playwright + testid registry) | §5.14 |

| Module reused | Purpose |
|---|---|
| `auth/encryption.py` (Fernet) | Decrypt Kite api_key + access_token + BYO keys (master key now Keychain-backed) |
| `backend/secret_loader.py` | `load_secret("byo_secret_key")` — extending the v1 algo Kite secret pattern |
| `backend/algo/paper/replay_rebuilder.py` | Auto-wired to startup in V2-0 (helper exists, not invoked in v1) |
| `backend/algo/paper/risk_engine.py` | Promoted from advisory to binding; v2-layer caps prepended |
| `backend/algo/broker/kite.py` | KiteClient extended; `place_order`/`cancel_order`/`modify_order` finally land |

| External | Purpose |
|---|---|
| Kite Connect REST API v3 | Order placement / cancellation / modification |
| Kite Connect Historical Data API | Gap-fill on WS reconnect (1m bars) |
| Kite Connect WebSocket v3 | Live tick stream |
