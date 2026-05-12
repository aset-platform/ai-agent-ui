# Algo Trading — Order Safety Hardening Design Spec

**Date:** 2026-05-12
**Author:** Abhay Kumar Singh
**Status:** Draft (awaiting user approval)
**Module:** Algo Trading — Live runtime + Kite broker client
**Predecessors:**
- `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md`
- `docs/superpowers/specs/2026-05-10-algo-v2-observability-postback-design.md`
**Working branch (proposed):** `feature/algo-order-safety-hardening`
**Target:** PR → `dev` (squash merge per `reference_git_merge_policy`)

---

## 1. Problem & Goals

Live trading shipped (PR #200, validated 2026-05-11 with real ITC fills) on a single hardcoded order path: aggressive LIMIT at `last_price ± 30 bps`, quantized to `0.05` tick, CNC-only, no broker-side SL. The 9-cap pre-trade gate in `backend/algo/live/safety.py:1-14` catches portfolio-level risk (max orders/day, max INR/day, max loss %, concentration, exposure) — but a class of order-level Kite footguns remains unguarded:

1. **Stale LTP** — `last_price` from `tick.last_price` is used blindly. If the WS feed froze (we've already documented this for `^BSESN`, see CLAUDE.md §6.5), the algo sends a LIMIT priced against a stale reference. Order either won't fill or fills at the wrong reference, consuming a daily cap slot.
2. **Liquidity-blind slippage** — 30 bps is sane for ITC / RELIANCE; it's reckless for an illiquid midcap (typical bid-ask 100-300 bps). Universe classification already exists (`backend/algo/universe/snapshot_job.py`) but order placement ignores it.
3. **In-flight order leak** — A LIMIT that doesn't fill in 60 s sits open, exposes us to directional risk, and counts against `max_orders_per_day`. Kite does not auto-cancel; we don't either.
4. **No pre-submit dedup** — Postback uses `guid` for idempotency on inbound events, but **outbound** placement has no guard. A retry loop bug or runtime double-tick would double-submit. Postback HMAC verify happens *after* the second order is alive.
5. **No freeze-qty awareness** — NSE imposes per-symbol freeze quantities. Submitting above → Kite rejection 200 ms later, but `max_orders_per_day` already incremented and the failed event still shows up in the audit trail as a rejection with no useful diagnostic.
6. **Thin order-submit audit** — `kite_client.place_order` logs ONE line via `_logger.info` (line 298-303): symbol, side, qty, order_type, kite_order_id. The full request params (price, exchange, variety, product, tag), Kite response dict, and pre-trade context (LTP used, slippage applied, freeze cap consulted) are not persisted. Postbacks land in `algo.events` with `raw` payload (great for debug); submissions do not (audit gap).

This spec closes all six in a single contained slice — broker-layer changes + one new event type, no schema migration.

### Goals

- **LTP staleness gate** — Reject placement at `kite_client.place_order` if the reference `last_price`'s `exchange_timestamp` is older than `MAX_LTP_AGE_SECONDS` (default 5 s). Caller (runtime) passes `last_price_ts` alongside `last_price`.
- **Per-liquidity-bucket slippage caps** — Replace single 30 bps constant in `runtime.py:533` with a lookup keyed on the universe `liquidity_bucket` snapshot: largecap 20 bps, midcap 50 bps, smallcap 100 bps, default 30 bps. Bucket cached on the strategy session.
- **Order TTL / auto-cancel** — Background asyncio task `_OrderTimeoutWatcher` per Live session that polls `kite.orders()` (already in the codebase for reconciliation) and issues `kite.cancel_order` for any session-tagged order older than `ORDER_TTL_SECONDS` (default 90 s) still in `OPEN` / `TRIGGER PENDING` state. Cancellation emits `order_cancelled_timeout` event.
- **Pre-submit duplicate guard** — Redis SETNX on `algo:placeorder:dedup:{user_id}:{strategy_id}:{symbol}:{side}:{qty}:{minute_bucket}` with 60 s TTL; raise `DuplicateOrderError` on miss-the-lock. Checked inside `place_order` BEFORE the SDK call. Dry-run skips the dedup check (dev convenience).
- **Freeze-quantity awareness** — On `kite_client.__init__` (or first-use), fetch `kite.instruments("NSE")` once-per-day, build a `dict[symbol → freeze_qty]` cached in Redis (`kite:freeze:{date}`, 24h TTL). `place_order` clamps and chunks orders above freeze qty (emits multiple sequential placements, each with a `chunk_index` tag). If chunking would exceed `max_orders_per_day`, the safety gate rejects upstream.
- **★ Full payload logging on placement** — Mirror the postback observability pattern from `webhooks/kite_postback.py`. Emit a new `order_submitted_live` event with the full request params dict AND the raw SDK response dict in `payload.raw`, written to `algo.events` (Iceberg) on every `place_order` call (real **and** dry-run). Surface in the existing Postback panel as a tab, or as a sibling panel "Order Submissions" (TBD in §3).

### Non-goals

- **No SL / SL-M support in this slice.** Kite's SL leg requires `variety="co"` (cover order) or `variety="bo"` (bracket — deprecated) and a re-think of the strategy AST. Tracked separately as the "broker-side protective stop" follow-up.
- **No MIS / NRML product support.** Hardcoded CNC stays. MIS gets its own slice (touches caps, reconciliation, runtime — too broad to fold here).
- **No per-ticker order-type switching** (LIMIT vs MARKET vs SL). Single LIMIT-primary path retained; only the *slippage tolerance* becomes ticker-aware.
- **No new strategy-AST nodes.** All hardening lives below the strategy layer.
- **No frontend rewrite.** One small addition: a tab/section on the Live Postback panel showing the new `order_submitted_live` events. No new pages.

---

## 2. Architecture

### 2.1 What changes

```
backend/algo/
├── broker/
│   ├── kite_client.py              # ★ place_order — staleness gate,
│   │                               #   dedup, freeze chunking, payload
│   │                               #   event emit
│   ├── freeze_cache.py             # ★ NEW — daily NSE instruments dump
│   │                               #   → {symbol: freeze_qty} via Redis
│   └── tests/
│       ├── test_kite_client_safety.py   # ★ NEW
│       └── test_freeze_cache.py         # ★ NEW
├── live/
│   ├── runtime.py                  # ★ pass last_price_ts to place_order,
│   │                               #   read liquidity_bucket from session,
│   │                               #   start _OrderTimeoutWatcher
│   ├── slippage.py                 # ★ NEW — bucket → bps lookup
│   ├── order_timeout.py            # ★ NEW — _OrderTimeoutWatcher task
│   └── tests/
│       ├── test_slippage_buckets.py
│       └── test_order_timeout.py
├── events.py                       # ★ add type_="order_submitted_live"
│                                   #     type_="order_cancelled_timeout"
│                                   #     type_="order_duplicate_blocked"
│                                   #     type_="order_ltp_stale_blocked"
│                                   #     type_="order_freeze_chunked"
└── routes/
    └── live.py                     # ★ GET /v1/algo/live/order-submissions
                                    #   (mirrors GET /postbacks shape)

frontend/components/algo-trading/live/
├── KitePostbackPanel.tsx           # ★ add "Submissions" tab OR
└── OrderSubmissionsPanel.tsx       # ★ NEW sibling panel — pick in §3.5

backend/algo/redis_keys.py          # ★ add dedup + freeze key helpers
```

**No DB schema changes.** All new events land in the existing `algo.events` Iceberg table (mode=`live`, new `type_` values listed above). Freeze cache is Redis-only (rebuilds daily on miss). Dedup is Redis-only (60 s TTL).

### 2.2 Data flow

**Order submission (new path):**

```
runtime._handle_signal()
  → reads liquidity_bucket from strategy session (loaded at session start
    from algo.universe_snapshot Iceberg table, keyed on ticker)
  → reads last_price + last_price_ts from latest WS tick
  → resolves slippage_bps = slippage.bps_for(bucket)  # 20 / 50 / 100 / 30
  → computes limit_price = last_price ± slippage_bps  (tick-quantized 0.05)
  → kite_client.place_order(
        ...,
        last_price=last_price,
        last_price_ts=last_price_ts,    # ★ NEW
        slippage_bps_applied=N,         # ★ NEW (for audit only)
    )

kite_client.place_order():
  1. If access_token None → RuntimeError
  2. If dry_run → emit order_submitted_live (synthetic id) + return
  3. ★ Staleness gate: if now - last_price_ts > MAX_LTP_AGE_SECONDS
     → emit order_ltp_stale_blocked + raise LtpStaleError
  4. Validate order_type / product / variety (existing)
  5. ★ Freeze chunking: if qty > freeze_cache.get(symbol)
     → split into ceil(qty / freeze_qty) sub-orders;
       emit order_freeze_chunked (chunks=[...])
     → loop steps 6-9 per chunk
  6. ★ Dedup: SETNX redis key (60 s); on miss
     → emit order_duplicate_blocked + raise DuplicateOrderError
  7. SDK place_order(...)
  8. ★ Emit order_submitted_live with full payload + response.raw
  9. Return order_id

_OrderTimeoutWatcher (asyncio task started by LiveRuntime.start):
  every ORDER_TIMEOUT_POLL_SECONDS (default 15s):
    orders = await asyncio.to_thread(kite.orders)
    for o in orders:
      if o.tag startswith f"algo-{strategy_id_prefix}"
         and o.status in {"OPEN", "TRIGGER PENDING"}
         and now - o.order_timestamp > ORDER_TTL_SECONDS:
        await asyncio.to_thread(kite.cancel_order,
          variety="regular", order_id=o.order_id)
        emit order_cancelled_timeout
```

**Frontend:** New tab on the existing `KitePostbackPanel` (or sibling — see §3.5) consumes `GET /v1/algo/live/order-submissions` (SWR 30 s, mirrors `useKitePostbacks` shape). One row per submission with expandable raw payload toggle.

---

## 3. Detailed Component Design

### 3.1 LTP staleness gate

**Where:** `backend/algo/broker/kite_client.py::place_order`, new kwarg `last_price_ts: datetime | None = None`. When `None` (legacy callers, tests) → gate skipped, warning logged.

**Logic:**
```python
MAX_LTP_AGE_SECONDS = 5  # configurable via env ALGO_MAX_LTP_AGE_S

if last_price_ts is not None:
    age = (datetime.now(UTC) - last_price_ts).total_seconds()
    if age > MAX_LTP_AGE_SECONDS:
        self._emit_event("order_ltp_stale_blocked", {
            "symbol": tradingsymbol,
            "last_price_ts": last_price_ts.isoformat(),
            "age_seconds": age,
            "max_age_seconds": MAX_LTP_AGE_SECONDS,
        })
        raise LtpStaleError(
            f"LTP age {age:.1f}s exceeds {MAX_LTP_AGE_SECONDS}s"
        )
```

**Runtime caller:** `runtime.py:535` already has `last_price`. WS tick already carries `exchange_timestamp` (see `TickEvent` model — verify). If not, add and propagate.

**Config:** `ALGO_MAX_LTP_AGE_S` env var, default 5. Override per-strategy NOT in scope (KISS).

### 3.2 Per-liquidity-bucket slippage caps

**New module:** `backend/algo/live/slippage.py`.

```python
DEFAULT_SLIPPAGE_BPS = {
    "largecap": 20,
    "midcap": 50,
    "smallcap": 100,
    "unknown": 30,    # current behavior — fallback for unclassified
}

def bps_for(liquidity_bucket: str | None) -> Decimal:
    return Decimal(
        DEFAULT_SLIPPAGE_BPS.get(
            (liquidity_bucket or "unknown").lower(),
            DEFAULT_SLIPPAGE_BPS["unknown"],
        )
    )
```

**Bucket source:** `algo.universe_snapshot` Iceberg table already classifies by `market_cap` and `adtv` (see `backend/algo/universe/snapshot_job.py:28-31`). Add a `liquidity_bucket` derived column:
- `largecap`: market_cap ≥ 20,000 cr AND in top-100 by market_cap
- `midcap`: 5,000 cr ≤ market_cap < 20,000 cr
- `smallcap`: market_cap < 5,000 cr (but passes 500 cr / 10 cr ADTV gate)

Bucket loaded into the strategy session on `LiveRuntime.start()` and cached on `self._bucket_by_ticker: dict[str, str]`. New tickers appearing mid-session default to `unknown` (30 bps).

**runtime.py change:**
```python
slippage_bps = slippage.bps_for(
    self._bucket_by_ticker.get(signal.ticker)
)
buffer = last_price * slippage_bps / BPS_DENOM
```

### 3.3 Order TTL / auto-cancel watcher

**New module:** `backend/algo/live/order_timeout.py`.

```python
class _OrderTimeoutWatcher:
    def __init__(
        self,
        kite_client: KiteClient,
        session_id: UUID,
        strategy_id: UUID,
        events_buffer: list[EventRow],
        ttl_seconds: int = 90,
        poll_seconds: int = 15,
    ): ...

    async def run(self) -> None:
        while not self._stopping:
            await self._tick_once()
            await asyncio.sleep(self._poll_seconds)

    async def _tick_once(self) -> None:
        orders = await asyncio.to_thread(self._kite.orders)
        tag_prefix = f"algo-{str(self._strategy_id)[:8]}"
        open_states = {"OPEN", "TRIGGER PENDING"}
        for o in orders:
            if not (o.get("tag") or "").startswith(tag_prefix):
                continue
            if o["status"] not in open_states:
                continue
            age = self._age(o["order_timestamp"])
            if age <= self._ttl_seconds:
                continue
            try:
                await asyncio.to_thread(
                    self._kite.cancel_order,
                    variety="regular",
                    order_id=o["order_id"],
                )
                self._emit("order_cancelled_timeout", o, age)
            except Exception as exc:
                self._emit_cancel_failed(o, exc)
```

**Started by** `LiveRuntime.start()` as `asyncio.create_task(...)`; stopped in `LiveRuntime.stop()`. One watcher per session (not per strategy — Kite `orders()` returns all; we filter by tag prefix).

**Config:** `ALGO_ORDER_TTL_S` (default 90), `ALGO_ORDER_TIMEOUT_POLL_S` (default 15). The TTL stays well under Kite's 200 orders/min cap.

**Edge case:** If a partial fill happened before cancellation, `cancel_order` only cancels the unfilled remainder. That's the desired behavior; the filled qty already exists in our position state via the normal fill path.

### 3.4 Pre-submit duplicate guard

**Redis key:**
```
algo:placeorder:dedup:{user_id}:{strategy_id}:{symbol}:{side}:{qty}:{minute_bucket}
```
Where `minute_bucket = floor(now_unix / 60)`. TTL 60 s. SETNX returns 1 if free, 0 if already taken.

**Inside `place_order`** (after staleness gate, before SDK call):
```python
if not self._dry_run:
    dedup_key = build_dedup_key(...)
    acquired = await asyncio.to_thread(
        self._redis.set, dedup_key, "1", nx=True, ex=60,
    )
    if not acquired:
        self._emit_event("order_duplicate_blocked", {
            "dedup_key": dedup_key,
            "symbol": tradingsymbol, "side": transaction_type,
            "qty": quantity,
        })
        raise DuplicateOrderError(dedup_key)
```

**Why minute_bucket and not strict idempotency?** Real algos legitimately re-submit BUY 100 ITC on the next bar (e.g. 5-min cadence). Cross-minute repeats are intentional and allowed. Same-minute repeats are the bug pattern we're catching.

**Dry-run skip:** `dry_run=True` short-circuits before the dedup check. Tests + dry-run UX stay frictionless.

### 3.5 Freeze-quantity cache + chunking

**New module:** `backend/algo/broker/freeze_cache.py`.

```python
_FREEZE_KEY_FMT = "kite:freeze:{date_ist}"
_FREEZE_TTL_S = 25 * 3600  # 25h — survives one missed refresh

async def get_freeze_qty(
    redis: Redis,
    kite_client: KiteClient,
    symbol: str,
) -> int | None:
    """Returns NSE per-order freeze quantity for symbol, or None
    if symbol not found (treat as no freeze cap)."""
    key = _FREEZE_KEY_FMT.format(date_ist=today_ist_iso())
    cached = await redis.hget(key, symbol)
    if cached is not None:
        return int(cached)
    # Refresh whole map (Kite returns a CSV-shaped list)
    instruments = await asyncio.to_thread(
        kite_client.kc.instruments, "NSE",
    )
    mapping = {
        i["tradingsymbol"]: int(i.get("freeze_qty") or 0)
        for i in instruments
    }
    if not mapping:
        return None
    await redis.hset(key, mapping=mapping)
    await redis.expire(key, _FREEZE_TTL_S)
    return mapping.get(symbol)
```

**Chunking in `place_order`** (only when freeze_qty > 0 and qty > freeze_qty):
```python
freeze_qty = await get_freeze_qty(self._redis, self, tradingsymbol)
if freeze_qty and quantity > freeze_qty:
    chunks = self._split_into_chunks(quantity, freeze_qty)
    self._emit_event("order_freeze_chunked", {
        "symbol": tradingsymbol,
        "total_qty": quantity,
        "freeze_qty": freeze_qty,
        "chunk_qtys": chunks,
    })
    order_ids: list[str] = []
    for idx, chunk_qty in enumerate(chunks):
        oid = self._place_single(
            tradingsymbol, ..., quantity=chunk_qty,
            tag=f"{tag}-c{idx}",
        )
        order_ids.append(oid)
    return order_ids[0]  # primary; rest visible via tag
```

**Edge case:** If `len(chunks)` would exceed remaining `max_orders_per_day`, raise `FreezeChunkExceedsDailyCapError` upstream of SDK call. Surface to UI as a single rejection event with `reason="freeze_chunk_exceeds_daily_cap"`.

**Note:** Kite `instruments` endpoint is unauthenticated and rate-limited; one call per day is well within budget.

### 3.6 ★ Order-submission payload logging

**The audit gap, by example:** today a rejected order leaves us with `_logger.error("live order rejected: symbol=%s ... reason=%s", ...)` and an `order_rejected_live` event whose payload omits the *request* we sent (price, slippage applied, freeze chunking decision). Debugging a Kite rejection in production means cross-referencing log timestamps with broker dashboards.

**New event type:** `order_submitted_live`. Emitted from `kite_client.place_order` on every submission (real + dry-run + every chunk of a freeze-chunked order).

**Payload schema:**
```python
{
  "session_id": "<uuid>",
  "user_id": "<uuid>",
  "strategy_id": "<uuid>",
  "internal_order_id": "<uuid>",        # caller-generated, links to fill
  "kite_order_id": "<id>" | "DRY_xxx",
  "dry_run": bool,
  "request": {                          # full submission params
    "tradingsymbol": "ITC",
    "exchange": "NSE",
    "transaction_type": "BUY",
    "quantity": 8,
    "order_type": "LIMIT",
    "product": "CNC",
    "variety": "regular",
    "price": 307.35,
    "tag": "algo-7c3a1b8d",
  },
  "context": {                          # pre-trade decision trace
    "last_price": 307.30,
    "last_price_ts": "2026-05-12T09:18:42.123+05:30",
    "ltp_age_seconds": 0.8,
    "liquidity_bucket": "largecap",
    "slippage_bps_applied": 20,
    "freeze_qty": 0,                    # 0 = no cap consulted
    "chunk_index": null,                # int when part of a chunk
    "chunk_total": null,
  },
  "response": {
    "raw": {...},                       # full SDK return dict
  },
  "submitted_at": "2026-05-12T09:18:42.987Z",
}
```

**Persistence:** Same `algo.events` Iceberg table that postbacks land in (`mode="live"`, new `type_="order_submitted_live"`). Inherits the existing 30-second flush, 14-month retention (TBD — confirm against `algo.events` retention policy).

**API:** `GET /v1/algo/live/order-submissions?limit=100&session_id=<id>` — mirrors the `GET /v1/algo/live/postbacks` shape from the postback spec. Same pagination + filter conventions.

**UI:** Pick ONE in implementation:
- **(a)** Add a "Submissions" tab to the existing `KitePostbackPanel` (DRY — one panel, three tabs: Submissions / Postbacks / Reconciliation drift). **Preferred.**
- **(b)** New sibling `OrderSubmissionsPanel`.

Decision in implementation: lean (a). Same raw-payload toggle UX as postbacks.

**Why this is worth the storage:** every single live order has a Kite rejection-or-fill story, and the most expensive bugs (wrong price, wrong qty, wrong product, wrong tag) are diagnosed by inspecting the exact request bytes. Logs rotate; Iceberg events don't.

---

## 4. Configuration

| Env var | Default | Description |
|---|---|---|
| `ALGO_MAX_LTP_AGE_S` | `5` | Reject submission if `last_price_ts` older than this |
| `ALGO_ORDER_TTL_S` | `90` | Cancel an open LIMIT after this many seconds |
| `ALGO_ORDER_TIMEOUT_POLL_S` | `15` | How often the watcher polls `kite.orders()` |
| `ALGO_DEDUP_TTL_S` | `60` | Pre-submit dedup window |
| `ALGO_SLIPPAGE_LARGECAP_BPS` | `20` | Override default largecap slippage cap |
| `ALGO_SLIPPAGE_MIDCAP_BPS` | `50` | — midcap — |
| `ALGO_SLIPPAGE_SMALLCAP_BPS` | `100` | — smallcap — |
| `ALGO_SLIPPAGE_UNKNOWN_BPS` | `30` | Fallback when bucket unknown |

All read in `backend/algo/config.py` (or wherever the existing live caps land — confirm in implementation).

---

## 5. Testing

### 5.1 Unit

- `test_kite_client_safety.py`:
  - LTP age within budget → submission proceeds.
  - LTP age over budget → `LtpStaleError`, event emitted.
  - Duplicate same-minute → `DuplicateOrderError`, event emitted.
  - Cross-minute same params → both succeed.
  - Dry-run skips dedup + staleness gates (only emits `order_submitted_live`).
  - `order_submitted_live` payload contains request + context + response.
- `test_slippage_buckets.py`: bucket → bps matrix; unknown → 30.
- `test_freeze_cache.py`: cache hit, miss → Kite refresh, chunk math.
- `test_order_timeout.py`: open + aged → cancelled; filled + aged → ignored; tagged for OTHER strategy → ignored; cancel failure → event emitted.

### 5.2 Integration

- `test_live_runtime_order_path.py`:
  - LiveRuntime calls `place_order` with `last_price_ts` populated.
  - Bucket lookup pulled from session.
  - End-to-end dry-run signal → `order_submitted_live` event in buffer with full payload.

### 5.3 E2E (Playwright)

- New test: place a paper/dry-run order in Live, navigate to KitePostbackPanel "Submissions" tab, expand row, assert raw payload toggle works and shows `request.price`.

### 5.4 Manual smoke (post-merge, market hours)

1. Arm a dry-run strategy on ITC. Confirm `order_submitted_live` events flow to Submissions panel with `dry_run=true` and `chunk_*=null`.
2. Flip dry_run off (one user, kill switch armed). Place a 1-share buy. Confirm event has real `kite_order_id` + real `response.raw`.
3. Force LTP staleness (pause WS for 10s, then re-evaluate) — confirm `order_ltp_stale_blocked` event, no Kite submission.
4. Force duplicate (script that calls `place_order` twice in <60 s) — confirm second is `order_duplicate_blocked`.
5. Stage a stale LIMIT (cancel WS before it fills) → wait 90 s → confirm `order_cancelled_timeout` event + Kite order in CANCELLED.

---

## 6. Rollout

1. **PR #1: Payload logging + staleness gate.** Lowest risk, highest debugging payoff. Ship behind `ALGO_MAX_LTP_AGE_S=999999` (effectively disabled) for first 24h while we confirm `last_price_ts` propagation correctness, then lower to 5.
2. **PR #2: Per-bucket slippage + bucket cache.** Behind `ALGO_SLIPPAGE_*` envs — overrideable per-environment.
3. **PR #3: Order TTL watcher.** Independent task; cancellation events make sense to land separately.
4. **PR #4: Dedup + freeze chunking.** Bundled because both touch the inner placement loop.

Each PR is a squash merge to `dev` (per `reference_git_merge_policy`).

**Backout plan:** Every gate is gated by an env var (or, in the dedup case, by `ALGO_DEDUP_TTL_S=0`). Disabling reverts to current behavior. Order TTL watcher disabled by setting `ALGO_ORDER_TTL_S=0` (watcher skips the cancellation step).

---

## 7. Open questions

1. **`last_price_ts` source on the tick stream.** Deferred to the PR #1 subagent — it has to grep `KiteWsMultiplexer` / `TickEvent` to wire `last_price_ts`, so confirmation lands inline with implementation. If `exchange_timestamp` isn't already on the tick model, the subagent extends it (small).

2. **`market_cap` source for liquidity-bucket classification.** **RESOLVED 2026-05-12: composite signal.**
   - `liquidity_bucket(ticker) = MORE_CONSERVATIVE_OF( bucket_by_mcap, bucket_by_adtv )`
   - `bucket_by_mcap` from `company_info.market_cap` (may be stale by days).
   - `bucket_by_adtv` from a nightly 20-day rolling ADTV computed off OHLCV — always fresh.
   - Boundary table (use whichever yields the higher slippage cap):
     | Bucket | Mcap threshold | ADTV threshold (20d rolling) |
     |---|---|---|
     | largecap | ≥ 20,000 cr AND top-100 by mcap | ≥ 50 cr/day |
     | midcap | 5,000 ≤ mcap < 20,000 cr | 20 ≤ ADTV < 50 cr/day |
     | smallcap | mcap < 5,000 cr | ADTV < 20 cr/day |
     | unknown | mcap missing/NaN | ADTV missing/NaN |
   - **Default on missing-either-side: smallcap** (100 bps — conservative).
   - **Default on missing-both: unknown** (30 bps — preserves today's behaviour for unclassified tickers; legitimate when a strategy adds a brand-new ticker mid-session).
   - Why conservative-wins: a smallcap that recently re-rated to ≥ 20k cr (rare but possible, e.g. post-stake-sale) still trades thin → ADTV catches it. A largecap that suddenly drops ADTV (corporate-action-driven volume drought) also gets caught.

3. **`freeze_qty` for cash equity rows.** **RESOLVED 2026-05-12: defensive defaults from NSE circulars when Kite returns null/0.**
   - Primary source: `kite.instruments("NSE")[i]["freeze_qty"]` per symbol.
   - When `freeze_qty in (None, 0)`: fall back to a static defaults table compiled from the latest NSE freeze-quantity circular, keyed on liquidity bucket (reuse Q2's bucket).
     | Bucket | Default freeze qty |
     |---|---|
     | largecap | 500,000 shares (most large caps allow generous freezes) |
     | midcap | 100,000 shares |
     | smallcap | 50,000 shares |
     | unknown | 50,000 shares (most conservative) |
   - Store the table as a hardcoded dict in `backend/algo/broker/freeze_cache.py::_NSE_DEFAULTS`. Annual review TODO (NSE updates the circular ~quarterly).
   - Log a `freeze_qty_fallback_applied` event on first use per (ticker, date) so we have visibility on how often we're guessing.
   - Why defensive defaults instead of "skip chunking": cost of a freeze rejection mid-algo (cap slot consumed, order rejected, audit-trail noise) > cost of a slightly-too-eager chunk split.

4. **Iceberg retention on `order_submitted_live` events.** **RESOLVED 2026-05-12: 12 months flat, all `algo.events` types.**
   - Volume estimate: ≤5 users × ~200 trades/day × ~5 events/trade ≈ 5,000 rows/day → ~1.8M rows/year ≈ ~3.5 GB raw at 2KB/row, ~700 MB compacted (Iceberg parquet+zstd).
   - Retention enforced in the existing `iceberg_maintenance` daily job — extend its `_RETENTION_DAYS` map with `("algo", "events"): 365`. (Grep `iceberg_maintenance.py` for the existing map shape before changing.)
   - Why 12 months and not differentiated per `type_`: simpler config, the storage delta is negligible (<1 GB/year), and the *most* useful debugging window for "what did we send Kite on Feb 12?" is the post-FY-close audit — 12 months covers that.
   - Aligns with `recommendation_runs` 14-month retention precedent (CLAUDE.md §5.8); slightly tighter because algo events are higher-frequency.

---

## 8. Jira shape (for ticket creation)

**Epic:** ASETPLTFRM-XXX — Order Safety Hardening (sub-epic of Algo Trading)
**Estimate:** 15 SP total (split into 4 sub-tasks below)
**Acceptance criteria** (sub-epic):
- All 6 hardening features behind env vars; defaults documented.
- 100% unit coverage on new modules; integration test asserts `order_submitted_live` payload shape.
- Live smoke (manual, market hours) signs off all 5 manual scenarios in §5.4.
- `PROGRESS.md` + CHANGELOG entry per CLAUDE.md §7.

**Sub-tasks:**
| Key | Title | SP |
|---|---|---|
| -1 | Payload logging (`order_submitted_live`) + LTP staleness gate | 4 |
| -2 | Per-bucket slippage caps + universe-bucket cache | 3 |
| -3 | Order TTL watcher + `order_cancelled_timeout` event | 3 |
| -4 | Pre-submit dedup + freeze-qty chunking | 5 |

Each ticket follows the 3-phase Jira lifecycle (`feedback_jira_ticket_lifecycle`): create with metadata → In Progress before code → comment with impl + Done at ship.
