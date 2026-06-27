# RSI(2) Connors Daily v5 — Three-Phase GTT Trailing Stop Exit Strategy

| | |
|---|---|
| Date | 2026-06-20 |
| Branch | `feature/rsi2-exit-strategy` |
| Builds on | `rsi2_connors_daily_v3.json` (paper-promotion-ready, all 5 gates pass) |
| Prior negative result | v4 mid-trade regime exit — documented in `docs/research/2026-05-23-rsi2-connors-v4-experiments.md` |
| Worktree | `/Users/abhay/Documents/projects/ai-agent-ui-rsi2-exit` |

---

## 1. Problem Statement

v3 exits when `distance_from_sma5 > 0` at bar close — the moment mean reversion begins. This
captures the first leg of recovery but misses continued upside when the position keeps running.
A stock can stay above SMA5 for days while the trailing stop ratchets up, capturing significantly
more of the move.

**Goal:** Replace the SMA5 bar-close exit with a three-phase GTT trailing stop that:
- Protects entry capital (Phase 1 hard stop, same as v3 `stop_loss_pct`)
- Reduces max loss when trade shows early promise then reverses (Phase 1.5 ratchet)
- Captures extended upside via ATR-based trail once position is profitable (Phase 2)
- Executes intraday via Kite GTT without blocking the existing signal engine

---

## 2. Strategy Arc

```
v1  baseline (SMA5 exit, 3% stop, unenforced)
v2  + ADTV ≥ ₹5Cr + regime gate + time-stop
v3  + 5% stop enforced + 7d cooldown  ✅ all 5 gates pass, paper-promotion ready
v4  mid-trade regime exit              ❌ negative result (see research doc)
v5  three-phase GTT trailing stop      ← THIS SPEC
```

---

## 3. Exit Design — Three Phases

All thresholds read from `risk.per_trade` in the strategy AST. No magic numbers in runtime code.

```
Entry @ price P  (RSI(2) ≤ 5, above SMA200, low stress, regime OK)
│
│  PHASE 1  ─────────────────────────────────────────────────
│  GTT placed immediately after BUY fill confirmed (postback)
│  stop = P × (1 − stop_loss_pct / 100)           e.g. P × 0.95
│
│  ↓ unrealised gain crosses phase1_ratchet_trigger_pct (+2%)
│
│  PHASE 1.5  ───────────────────────────────────────────────
│  ONE-TIME ratchet — GTT cancel + replace
│  new_stop = P × (1 − phase1_ratchet_new_stop_pct / 100)   e.g. P × 0.97
│  phase1_ratcheted = True  (never fires again)
│  Worst-case loss reduced from −5% → −3%
│
│  ↓ unrealised gain crosses trailing_trigger_pct (+5%)
│
│  PHASE 2  ──────────────────────────────────────────────────
│  ATR-based trailing — GTT ratchets up with every new HWM
│  trail_width = ATR(14) × trailing_atr_multiplier   e.g. ATR × 1.5
│  new_stop = max(current_stop, HWM − trail_width)
│  GTT ratchets every 15 minutes when HWM advances
│  Guaranteed minimum profit when phase 2 first fires:
│    +5% trigger − ~3.5% ATR trail = +1.5% locked in
│
└─ SAFETY: time-stop at max_holding_days (5d) from AST → forced SELL
           cooldown_after_failed_exit_days (7d) from AST → repeat-offender gate
```

### Concrete Example

Entry ₹5,000, ATR(14) = ₹125 (2.5%), `stop_loss_pct=5`, `phase1_ratchet_trigger_pct=2`,
`phase1_ratchet_new_stop_pct=3`, `trailing_trigger_pct=5`, `trailing_atr_multiplier=1.5`:

```
Entry          ₹5,000   GTT → ₹4,750 (Phase 1, −5%)
+2% reached    ₹5,100   GTT → ₹4,850 (Phase 1.5, −3%) [one-time]
+5.2% reached  ₹5,260   Phase 2 kicks in
                         trail_width = ₹187.50
                         GTT → ₹5,072 (+1.45% locked in)
Day 3 HIGH     ₹5,400   GTT ratchets → ₹5,212
Afternoon      ₹5,180   GTT fires → exit ₹5,212  (+4.24%)

v3 SMA5 exit would have fired at ~₹5,060 on Day 2  (+1.2%)
v5 captures ₹5,212  (+4.24%) on the same trade
```

### Cooldown Assignment

| Exit reason | Cooldown |
|---|---|
| `phase1_stop` | `cooldown_after_failed_exit_days` (7d) |
| `phase1_ratchet` | `cooldown_after_failed_exit_days` (7d) |
| `trail_stop` | 0 — thesis worked, ticker immediately re-enterable |
| `time_stop` | `cooldown_after_failed_exit_days` (7d) |

---

## 4. AST Changes

### 4.1 New fields in `RiskPerTrade` (`backend/algo/strategy/ast.py`)

```python
class RiskPerTrade(BaseModel):
    # existing
    stop_loss_pct: float = 0.0
    max_qty: int = 10000
    max_holding_days: int | None = None
    cooldown_after_failed_exit_days: int | None = None

    # v5 — all optional; None = trailing logic disabled (v1/v2/v3 unchanged)
    phase1_ratchet_trigger_pct: float | None = None   # +2.0
    phase1_ratchet_new_stop_pct: float | None = None  # 3.0 (absolute from entry)
    trailing_trigger_pct: float | None = None          # +5.0
    trailing_atr_multiplier: float | None = None       # 1.5
```

Older templates have none of these fields → runtime skips trailing entirely → zero behaviour change
for v1/v2/v3.

### 4.2 v5 Template diff from v3

Two changes only:
1. `else` branch becomes `{"type": "hold"}` — SMA5 exit removed; GTT owns the exit
2. Four new `per_trade` fields added

```json
"root": {
  "type": "if",
  "cond": { "type": "and", "operands": [ /* identical to v3 */ ] },
  "then": {"type": "set_target_weight", "weight": 0.20},
  "else": {"type": "hold"}
},
"risk": {
  "per_trade": {
    "stop_loss_pct": 5.0,
    "max_qty": 10000,
    "max_holding_days": 5,
    "cooldown_after_failed_exit_days": 7,
    "phase1_ratchet_trigger_pct": 2.0,
    "phase1_ratchet_new_stop_pct": 3.0,
    "trailing_trigger_pct": 5.0,
    "trailing_atr_multiplier": 1.5
  }
}
```

---

## 5. Architecture

### 5.1 Layer diagram

```
┌─────────────────────────────────────────────────────────┐
│  Strategy AST  (JSON template)                          │
│  4 new optional fields in risk.per_trade                │
│  v3 = unchanged · v5 opts in                            │
└────────────────────┬────────────────────────────────────┘
                     │ parsed by ast.py → RiskPerTrade
┌────────────────────▼────────────────────────────────────┐
│  TrailingStopManager  (new pure module)                 │
│  backend/algo/backtest/trailing_stop_manager.py         │
│  Per-position phase state + HWM tracking                │
│  Emits: STOP_UPDATED | STOP_HIT                         │
└──────────┬─────────────────┬──────────────────┬─────────┘
           │                 │                  │
    ┌──────▼──────┐  ┌───────▼───────┐  ┌──────▼──────────┐
    │  Backtest   │  │  Paper +      │  │  Live           │
    │  SimBroker  │  │  Dry-run      │  │  Runtime +      │
    │             │  │  PaperBroker  │  │  GTT Client     │
    │ daily OHLC  │  │ 15m bars      │  │ KiteTicker WS   │
    │ LOW→HIGH    │  │ (replay for   │  │ 15-min timer    │
    │ conservative│  │  dry-run)     │  │ GTT cancel+     │
    │             │  │ no GTT calls  │  │ replace         │
    └─────────────┘  └───────────────┘  └─────────────────┘
```

### 5.2 TrailingStopManager (pure module, no I/O)

```python
# backend/algo/backtest/trailing_stop_manager.py

@dataclass
class TrailingStopState:
    ticker: str
    entry_price: float
    phase: int              # 1=hard-stop, 15=ratcheted (phase 1.5), 2=ATR-trail
    current_stop: float
    hwm: float
    phase1_ratcheted: bool
    atr: float              # ATR(14) at entry, fixed throughout

class TrailingStopManager:
    def __init__(self, risk: RiskPerTrade, entry_price: float, atr: float): ...

    def on_price_update(self, price: float) -> TrailingEvent | None:
        """
        Call on every price observation (tick, bar.high, etc).
        Returns STOP_UPDATED (new stop price), STOP_HIT, or None.

        Internal logic (evaluated in order):
          1. hwm = max(hwm, price)
          2. unrealised_pct = (price - entry) / entry * 100
          3. Phase 1 → 1.5: if not ratcheted and unrealised >= ratchet_trigger
          4. Phase 1.5 → 2: if unrealised >= trailing_trigger
          5. Phase 2 HWM advance: new_stop = max(stop, hwm - atr*multiplier)
          6. Stop hit: if price <= current_stop → STOP_HIT
        """
```

**Runtime handlers:**

| Event | Backtest | Paper / Dry-run | Live |
|---|---|---|---|
| `STOP_UPDATED` | Record new stop in sim state | Record in broker state; dry-run logs intent | Cancel old GTT → place new GTT |
| `STOP_HIT` | Simulate fill at `current_stop` | Simulate fill at next bar open | GTT fires on Kite (postback handles it) |

---

## 6. Runtime Implementations

### 6.1 Backtest (SimBroker)

Uses daily OHLC. Conservative worst-case ordering per bar (LOW before HIGH):

```
On BUY fill (T+1 open):
  → TrailingStopManager(risk_config, fill_price, daily_atr_at_entry)

Per subsequent daily bar:
  1. if bar.low <= manager.current_stop:
       exit at current_stop (or bar.low if gapped past)
       apply cooldown per §3 rules
       DONE

  2. manager.on_price_update(bar.high)   ← HWM + phase transition
     if STOP_UPDATED: record new stop

  3. if holding_days >= risk.max_holding_days:
       exit at bar.close, exit_reason=time_stop
```

`StopLossMonitor` (flat % stop) is bypassed for trailing-enabled strategies.
`TimeStopMonitor` continues unchanged — reads `max_holding_days` exactly as today.
ATR source: `stocks.analysis_summary` ATR(14) at entry date.

### 6.2 Paper + Dry-run (PaperBroker)

Uses actual 15m bars from `algo.intraday_bars`. Same cadence as live timer (15 min).
No real GTT calls in either mode.

```
On BUY fill:
  → TrailingStopManager(risk_config, fill_price, atr)
  → dry-run logs: "[DRY-RUN] would place_gtt trigger=X limit=Y"

Per 15m bar:
  manager.on_price_update(bar.high)
  if STOP_UPDATED:
    record new stop
    dry-run logs: "[DRY-RUN] would ratchet_gtt old=X new=Y"

  if bar.low <= manager.current_stop:
    simulate fill at current_stop (+ 1% slippage equiv for realism)
    dry-run logs: "[DRY-RUN] would gtt_fire at X"

At 15:25 IST daily:
  time-stop check: holding_days >= max_holding_days → forced exit
```

All dry-run events written to `algo.events` with `dry_run=True` in payload.
Fully observable in the events window — operators see every GTT call that would have fired.

### 6.3 Live (Runtime + GTT)

**Three wiring points:**

**Wiring 1 — BUY fill postback** (`kite_postback.py`):
```python
# order_filled_live, side=BUY
fill_price = event.average_price
atr = feature_cache.get(ticker, "atr_14")
manager = TrailingStopManager(risk, fill_price, atr)

stop_price = manager.current_stop
gtt_id = kite.place_gtt(
    trigger   = stop_price,
    limit     = stop_price * (1 - GTT_LIMIT_HEADROOM_PCT),  # 0.99
    qty       = position.quantity,
    side      = "SELL",
)
trailing_states[ticker] = manager
gtt_ids[ticker] = gtt_id
Redis.set(f"trailing:{uid}:{sid}:{ticker}", serialise(manager, gtt_id))
log algo.events: gtt_placed, phase=1, stop=stop_price, gtt_id=gtt_id
```

**Wiring 2 — 15-minute timer** (new `asyncio.Task` in live runtime):
```python
# Runs every 15 min during 09:15 → 15:25 IST
# Aligned to 15m bar boundaries (09:15, 09:30, 09:45 ...)

for ticker, manager in trailing_states.items():
    price = ws_hwm[ticker]          # HWM tracked from WS ticks (lightweight)

    event = manager.on_price_update(price)

    if event == STOP_UPDATED:
        kite.delete_gtt(gtt_ids[ticker])
        new_id = kite.place_gtt(
            trigger = manager.current_stop,
            limit   = manager.current_stop * 0.99,
            qty     = position.quantity, side="SELL",
        )
        gtt_ids[ticker] = new_id
        Redis.set(f"trailing:{uid}:{sid}:{ticker}", serialise(manager, new_id))
        log algo.events: gtt_ratcheted, phase, old_stop, new_stop, gtt_id_new

    if event == STOP_HIT:           # only possible if WS was down during stop hit
        kite.delete_gtt(gtt_ids[ticker])
        kite.place_limit_sell(ticker, manager.current_stop)

# Lightweight WS tick callback (no GTT logic, HWM only):
def on_tick(ticker, last_price):
    if ticker in trailing_states:
        ws_hwm[ticker] = max(ws_hwm.get(ticker, 0.0), last_price)
```

**Wiring 3 — GTT fill postback + recovery**:
```python
# GTT fires (Kite postback):
del trailing_states[ticker]
Redis.delete(f"trailing:{uid}:{sid}:{ticker}")
position_tracker.mark_closed(ticker)
log algo.events: gtt_fired_exit, exit_price, phase, exit_reason

# WS reconnect / runtime restart:
for ticker in open_positions:
    quote = kite.quote(ticker)          # day's HIGH so far
    manager = Redis.get(f"trailing:{uid}:{sid}:{ticker}")
    manager.on_price_update(quote.high) # catch up on missed HWM
    if ticker not in kite.get_gtts():   # GTT fired while down
        check kite.orders() for fill → process as exit
    else:
        verify GTT trigger matches manager.current_stop → re-place if stale

# Bar-close 15:25 IST (modified for v5):
for ticker in open_positions with trailing state:
    manager.on_price_update(bar.high)  # daily HIGH = full-day precision ratchet
    if STOP_UPDATED: ratchet GTT
    if holding_days >= risk.max_holding_days:
        kite.delete_gtt(gtt_ids[ticker])
        kite.place_limit_sell(ticker, bar.close)
        log algo.events: gtt_cancelled_for_time_stop
```

### 6.4 GTT execution — limit headroom

Kite GTT only supports LIMIT orders for the triggered sell (no SL-M). To ensure fill on fast moves:

```
GTT_LIMIT_HEADROOM_PCT = 0.01   (runtime config constant, not per-strategy)

trigger_price = computed stop level
limit_price   = trigger_price × (1 - GTT_LIMIT_HEADROOM_PCT)
             = trigger_price × 0.99
```

Covers normal intraday fast moves. Extreme gap-downs (>1%) result in non-fill:
- `gtt_limit_unexecuted` event fired if LIMIT unfilled 30 min after trigger
- Time-stop (max 5 days from AST) cleans up any position not closed by GTT
- Upgrade path: SL-M support already flagged as deferred in `KiteClient` (`_ALLOWED_ORDER_TYPES`)

### 6.5 GTT coordination — no double exits

```
Case A: GTT fires intraday
  → postback → position_tracker.mark_closed(ticker)
  → 15:25 signal engine sees no position → no SELL placed ✅

Case B: Bar-close time-stop fires at 15:25
  → cancel GTT first (synchronous, wait for confirmation)
  → then place LIMIT SELL ✅

Case C: Both attempt simultaneously
  → GTT fires first → position CLOSED
  → LIMIT SELL rejected by Kite ("insufficient holdings") → logged as warning ✅
```

GTT state is source of truth. Before any regular exit order: check GTT state.

---

## 7. New KiteClient Methods

Three additions to `backend/algo/broker/kite_client.py`:

```python
def place_gtt(
    self,
    ticker: str,
    instrument_token: int,
    trigger_price: float,
    limit_price: float,
    qty: int,
    transaction_type: str = "SELL",
) -> int:
    """Place a GTT stop order. Returns gtt_id."""

def delete_gtt(self, gtt_id: int) -> None:
    """Cancel a GTT order. No-op if already triggered."""

def get_gtts(self) -> list[dict]:
    """List all active GTTs for the account."""
```

---

## 8. Event Vocabulary (`algo.events`)

All events carry `user_id`, `strategy_id`, `ticker`, `mode` (paper/dryrun/live), `ts_ns`.

| `type_` | When fired | Key payload fields |
|---|---|---|
| `gtt_placed` | BUY fill confirmed | `phase=1`, `entry_price`, `stop_price`, `limit_price`, `gtt_id` |
| `gtt_ratcheted` | STOP_UPDATED from manager | `phase`, `old_stop`, `new_stop`, `hwm`, `gtt_id_old`, `gtt_id_new` |
| `trailing_phase_transition` | Phase 1→1.5 or 1.5→2 | `from_phase`, `to_phase`, `price_at_transition`, `new_stop` |
| `gtt_fired_exit` | GTT triggered + fill confirmed | `exit_price`, `phase`, `exit_reason` |
| `gtt_cancelled_for_time_stop` | `max_holding_days` reached | `holding_days`, `gtt_id` |
| `gtt_limit_unexecuted` | LIMIT unfilled 30 min post-trigger | `gtt_id`, `trigger_price`, `limit_price`, `ltp_at_check` |
| `trailing_stop_recovered` | Restart — state rehydrated from Redis | `phase`, `hwm_recovered`, `gtt_verified` |

Dry-run: all events include `"dry_run": true` in payload. Fully observable in events window.

---

## 9. Redis State Schema

One key per open trailing position. TTL 2 trading days (auto-expires after close).

```
Key:   trailing:{user_id}:{strategy_id}:{ticker}
Value: {
  phase: int,                 # 1 | 15 | 2
  entry_price: float,
  current_stop: float,
  hwm: float,
  atr: float,
  phase1_ratcheted: bool,
  holding_days: int,
  gtt_id: int
}
```

---

## 10. Promotion Path

```
v5 draft
  ↓ backtest (daily OHLC, conservative LOW-first, full 4.4yr window 2022→2026)
  ↓ walk-forward DSR ≥ 0.95  (required for paper promotion)
  ↓ paper (15m bars, real market, minimum 30 days)
  ↓ dry-run (replay last 60 days, 15m bars, validate GTT event log)
  ↓ live (GTT live, CNC, ₹5L NAV)
```

Backtest note: daily OHLC simulation is approximate (doesn't know intraday bar order within a day).
Conservative LOW-before-HIGH assumption makes numbers slightly pessimistic — a trustworthy lower bound.
Paper mode (real 15m bars) provides the precise validation before capital is at risk.

---

## 11. Files Touched

| File | Change |
|---|---|
| `backend/algo/strategy/ast.py` | Add 4 optional fields to `RiskPerTrade` |
| `backend/algo/backtest/trailing_stop_manager.py` | **New** — pure state machine |
| `backend/algo/backtest/sim_broker.py` | Add trailing stop path (replaces flat stop for v5) |
| `backend/algo/paper/broker.py` | Add 15m-bar trailing stop evaluation |
| `backend/algo/broker/kite_client.py` | Add `place_gtt`, `delete_gtt`, `get_gtts` |
| `backend/algo/live/runtime.py` | GTT wiring: postback handler, 15-min timer, recovery |
| `backend/algo/webhooks/kite_postback.py` | On BUY fill: init manager + place GTT |
| `backend/algo/strategy/templates/rsi2_connors_daily_v5.json` | **New** template |
| `backend/algo/backtest/tests/test_trailing_stop_manager.py` | **New** unit tests |
| `backend/algo/backtest/tests/test_exit_reason_propagation.py` | Extend for v5 exit reasons |
| `backend/algo/live/tests/test_gtt_trailing_integration.py` | **New** live integration test |

---

## 12. Open Questions / Future Work

- **Backtest ATR source**: using `stocks.analysis_summary` ATR(14) at entry date. Verify this field
  is populated for the full 2022→2026 window. If gaps exist, fall back to 14-day rolling ATR computed
  from daily bars directly in SimBroker.
- **SL-M upgrade**: when `KiteClient` adds SL-M support (deferred in codebase), GTT limit headroom
  can be removed and replaced with market execution on trigger. Zero code change to `TrailingStopManager`.
- **v5 parameter sweep**: after initial backtest confirms positive gate pass, run a sweep over
  `trailing_atr_multiplier` ∈ {1.0, 1.5, 2.0} and `trailing_trigger_pct` ∈ {3.0, 5.0, 7.0}
  to find the optimal combination. Use existing walk-forward sweep infrastructure.
- **Volatility-adjusted position sizing (E2)**: from v4 research doc — smaller weight when regime
  hostile. Compatible with v5 (separate `set_target_weight` modifier, not exit logic). File separately.
