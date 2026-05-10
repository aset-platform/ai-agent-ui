# Algo Trading v2 — Observability + Kite Postback Design Spec

**Date:** 2026-05-10
**Author:** Abhay Kumar Singh
**Status:** Draft (awaiting user approval)
**Module:** Algo Trading (v2 follow-ups)
**Predecessor:** `docs/superpowers/specs/2026-05-09-algo-trading-v2-design.md`
**Working branch:** `feature/algo-trading-v2-integration` (current; not yet merged to `dev`)
**Research:**
- `docs/superpowers/research/2026-05-10-kite-postback-ngrok.md`
- `docs/superpowers/research/2026-05-10-codebase-regime-factor-inventory.md`

---

## 1. Problem & Goals

v2 shipped Live trading on `feature/algo-trading-v2-integration` but two safety/observability features were either deferred from v1 or surfaced as gaps during yesterday's walkthrough:

1. **Order-status fast path** — today the Live runtime polls `kite.orders()` to learn that a placed order has filled / been rejected / cancelled. Polling is wasteful, has latency variance, and contributes nothing to the `algo.events` audit trail. Kite publishes a postback URL feature designed for exactly this.
2. **WS heartbeat visibility** — `KiteWsMultiplexer` exposes `connected`, `subscriber_count`, last tick state as in-memory properties on a singleton. Nothing surfaces them via API or UI. Verifying "is the WS alive right now?" during the Mon 09:15 IST smoke means tailing logs for `tick #N` lines, which doesn't scale and isn't durable.

This spec adds both — small, contained, low-risk, and wraps up the v2 epic before the integration → `dev` PR.

### Goals

- **Kite postback handler** at `POST /v1/webhooks/kite/postback` with SHA-256 checksum verification (per [Kite Connect v3 docs](https://kite.trade/docs/connect/v3/postbacks/)), `guid`-based idempotency, dispatched into the existing `algo.events` log alongside Live runtime events.
- **ngrok dev tunnel** as a `docker-compose` service so the same postback URL survives `backend` restarts and can be registered ONCE in the Kite Developer Console (the URL is per-app, not per-user — manual console edit, no API).
- **WS health endpoint** `GET /v1/algo/live/ws-health` that surfaces multiplexer in-memory state (connected, subscriber_count, last_tick_at, tick_age_seconds, subscribed_tokens, tick_count_today) without forcing the multiplexer into existence.
- **Frontend WS status dot** in the Live segment header — green / amber / red traffic light driven by `tick_age_seconds`, polled 10s via SWR.
- **Postback observability panel** on the Live tab — last 50 postbacks with raw payload toggle (mirrors the existing event timeline UX from V2-3).
- **Reconciliation stays the source of truth.** Postbacks are advisory/fast-path; the V2-3 reconciliation loop continues to run every 5 min and a postback being lost (no documented Kite retry semantics) does not produce silent state drift.

### Non-goals

- **No automatic Kite postback URL update from code.** It's manually configured per-app in the Developer Console; spec'd that way.
- **No Cloudflare Tunnel migration in this spec.** ngrok free + 1 dev domain is sufficient for the dev/test cycle. Cloudflare Tunnel is the documented production handoff path but not built here.
- **No postback-driven fill ingestion bypass of the runtime.** Live runtime continues to receive fills via its existing path; postbacks are an *additional* event channel for visibility + reconciliation, not a replacement.
- **No new event types beyond the postback channel.** Existing `order_filled_live`, `order_rejected_live`, `order_cancelled_live` event types stay; postback adds `kite_postback_received`.
- **No retry/queue infrastructure for postbacks.** Single-instance, 5 users, ≤200 postbacks/day max — synchronous handler is fine. If the request errors, Kite drops it (no retries published) and the next reconciliation run catches the drift.

---

## 2. Architecture

### 2.1 What changes

```
backend/algo/
├── broker/
│   └── ws_multiplexer.py          # add public read-only properties + last_tick_at
├── live/
│   └── (no changes)
├── webhooks/                       # ★ NEW package
│   ├── __init__.py
│   ├── kite_postback.py            # ★ NEW — verifier + payload model
│   └── tests/
│       └── test_kite_postback.py
├── routes/
│   ├── live.py                     # ★ add GET /ws-health, GET /postbacks
│   └── webhooks.py                 # ★ NEW — POST /webhooks/kite/postback (mounted under /v1)

frontend/
├── components/algo-trading/
│   ├── LiveWsHealthDot.tsx         # ★ NEW — traffic-light dot + tooltip
│   ├── KitePostbackPanel.tsx       # ★ NEW — last 50 postbacks list
│   └── PaperTab.tsx                # ★ mount LiveWsHealthDot in Live segment header
├── hooks/
│   ├── useWsHealth.ts              # ★ NEW — SWR 10s
│   └── useKitePostbacks.ts         # ★ NEW — SWR 30s

docker-compose.yml                  # ★ NEW ngrok service (profile: live)
.env.example                        # ★ NGROK_AUTHTOKEN, NGROK_DOMAIN
docs/algo-trading/postbacks.md      # ★ NEW — operator runbook
```

No DB schema changes. Postback events land in the existing `algo.events` Iceberg table (mode=`live`, type=`kite_postback_received`). Multiplexer health reads in-memory only — no persistence.

### 2.2 Data flow

**Postback path:**
```
Kite OMS  ──POST──►  ngrok ──►  backend:8181 /v1/webhooks/kite/postback
                                       │
                                       ├─ verify SHA-256(order_id+order_timestamp+api_secret) == checksum
                                       ├─ dedup: SELECT 1 FROM events WHERE payload->>'guid' = $1 LIMIT 1
                                       ├─ persist event_row(mode='live', type='kite_postback_received', payload=full)
                                       └─ 200 OK (under 3s)
```

**WS health path:**
```
Frontend SWR(10s)  ──►  GET /v1/algo/live/ws-health  ──►  ws_multiplexer_registry
                                                                  │
                                                                  ├─ if no mux for user: connected=false
                                                                  └─ else: snapshot of mux properties
```

### 2.3 Why postbacks AND reconciliation, not one or the other

| Channel | Latency | Reliability | Auth | Use |
|---|---|---|---|---|
| Postback | <1s | Best-effort, no Kite retries documented | Per-app SHA-256 secret | Fast UX feedback (UI marks order COMPLETE in real time) |
| Reconciliation (V2-3) | up to 5min | Periodic poll, broker is source of truth | Per-user OAuth token | Catches anything postbacks lost; final word on positions |
| Live runtime polling (today) | 1-2s | Reliable but wasteful | Per-user OAuth | DEPRECATED once postbacks land (kept as fallback if `KITE_POSTBACK_ENABLED=false`) |

Postbacks reduce polling cost + UI latency. Reconciliation guarantees correctness. Both, not either.

---

## 3. Modules

### 3.1 Postback verifier — `backend/algo/webhooks/kite_postback.py`

```python
import hashlib
import hmac
from pydantic import BaseModel, Field

class KitePostbackPayload(BaseModel):
    """Subset of Kite postback fields we persist. Full payload still
    stored verbatim in event payload['raw'] for forensics."""
    user_id: str
    order_id: str
    exchange_order_id: str | None = None
    status: str  # COMPLETE | REJECTED | CANCELLED | UPDATE
    status_message: str | None = None
    tradingsymbol: str
    instrument_token: int
    exchange: str
    transaction_type: str
    order_type: str
    product: str
    quantity: int
    filled_quantity: int
    unfilled_quantity: int
    cancelled_quantity: int
    price: float
    trigger_price: float
    average_price: float
    order_timestamp: str  # IST "YYYY-MM-DD HH:MM:SS", no TZ — hash verbatim
    checksum: str
    tag: str | None = None
    guid: str  # idempotency key

def verify_checksum(payload: dict, api_secret: str) -> bool:
    """SHA-256(order_id + order_timestamp + api_secret) == checksum.

    NOT HMAC despite the visual similarity — Kite mixes secret into the
    hashed string. Use hmac.compare_digest for constant-time compare."""
    order_id = payload.get("order_id", "")
    order_ts = payload.get("order_timestamp", "")
    expected = hashlib.sha256(
        f"{order_id}{order_ts}{api_secret}".encode("utf-8")
    ).hexdigest()
    received = (payload.get("checksum") or "").lower()
    return hmac.compare_digest(expected, received)
```

### 3.2 Postback route — `backend/algo/routes/webhooks.py`

Mounted at `/v1/webhooks/kite/postback`. **NOT** behind any auth middleware (Kite is the caller — auth is the checksum). Rate-limited via existing `slowapi` middleware (default 60 req/min per IP).

```python
@router.post("/webhooks/kite/postback", status_code=200)
async def kite_postback(request: Request) -> dict:
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid json")

    api_secret = load_secret("kite_api_secret")
    if not api_secret:
        # Fail-closed: 503 if secret not configured (mirrors v1
        # webhook signature pattern from Razorpay)
        raise HTTPException(503, "kite api secret not configured")

    if not verify_checksum(payload, api_secret):
        _logger.warning("kite postback checksum failed")
        raise HTTPException(401, "bad checksum")

    guid = payload.get("guid", "")
    if not guid:
        raise HTTPException(400, "missing guid")

    # Idempotency: dedup on guid via Iceberg query (cheap; events
    # table indexed on payload->>'guid' via DuckDB read).
    if await _is_duplicate(guid):
        return {"ok": True, "deduplicated": True}

    # Resolve user_id (kite user_id → our user.id) for event persistence.
    our_user_id = await _resolve_kite_user(payload["user_id"])

    # Persist into events table — same pattern as live runtime fills.
    from backend.algo.backtest.event_writer import event_row, flush_events
    row = event_row(
        session_id=None,
        user_id=our_user_id,
        strategy_id=None,  # postback is order-scoped, not strategy-scoped
        mode="live",
        type_="kite_postback_received",
        payload={
            "guid": guid,
            "order_id": payload["order_id"],
            "status": payload["status"],
            "filled_quantity": payload.get("filled_quantity", 0),
            "average_price": payload.get("average_price", 0.0),
            "tradingsymbol": payload["tradingsymbol"],
            "raw": payload,  # full payload for forensics
        },
    )
    flush_events([row])
    return {"ok": True}
```

**Constraints:**
- Handler completes under 3s (read body + verify + 1 dedup query + 1 flush). Real work (drift comparison) deferred to reconciliation cron.
- If reconciliation needs to know about the postback before its next 5-min cycle, it can read `algo.events` filtered by `payload->>'guid'` — no new state surface.
- `user_id` resolution: Kite postback `user_id` is the Zerodha client ID (e.g. `"AB1234"`). We map via `auth.broker_credentials.kite_client_id` (already populated during OAuth). Lookup cached in Redis (5min TTL) since the mapping is static.

### 3.3 ngrok service — `docker-compose.yml`

Adds an `ngrok` service under a new `live` profile so it ONLY runs when `--profile live` is passed (avoids tunneling on every dev start):

```yaml
ngrok:
  image: ngrok/ngrok:latest
  restart: unless-stopped
  profiles: ["live"]
  command:
    - "http"
    - "--domain=${NGROK_DOMAIN}"
    - "backend:8181"
  environment:
    NGROK_AUTHTOKEN: ${NGROK_AUTHTOKEN}
  ports:
    - "4040:4040"   # inspection UI
  depends_on:
    - backend
```

`.env.example` additions:
```
# Kite postback receiver (live trading only)
NGROK_AUTHTOKEN=                  # https://dashboard.ngrok.com → Your Authtoken
NGROK_DOMAIN=                     # e.g. abhay-aiagent.ngrok-free.dev (free tier 1 domain)
KITE_POSTBACK_ENABLED=false       # default off; flip true after Kite console URL set
```

Operator flow (one-time, documented in `docs/algo-trading/postbacks.md`):
1. Sign up ngrok.com → copy authtoken.
2. Dashboard → Domains → claim free `*.ngrok-free.dev`.
3. Paste both into `.env`.
4. `docker compose --profile live up -d ngrok`.
5. Kite Developer Console → app → Postback URL → `https://<NGROK_DOMAIN>/v1/webhooks/kite/postback`.
6. Set `KITE_POSTBACK_ENABLED=true` in `.env`, restart backend.
7. Trigger a tiny test order in Dry-run mode — verify `kite_postback_received` event lands within 2s in the events panel + at `http://localhost:4040`.

### 3.4 WS health endpoint — `backend/algo/routes/live.py`

New endpoint added to existing `live.py` router:

```python
class WsHealth(BaseModel):
    connected: bool
    subscriber_count: int
    subscribed_tokens: int
    last_tick_at: str | None  # ISO 8601 UTC with Z
    tick_age_seconds: int | None
    tick_count_today: int

@router.get("/ws-health", response_model=WsHealth)
async def get_ws_health(
    user: UserContext = Depends(pro_or_superuser),
) -> WsHealth:
    """Snapshot of the per-user WS multiplexer in-memory state.

    Does NOT create a multiplexer if none exists — returns
    connected=false instead. This avoids spurious WS connections from
    health probes."""
    from backend.algo.broker.ws_registry import (
        get_multiplexer_if_exists,
    )
    mux = get_multiplexer_if_exists(UUID(user.user_id))
    if mux is None:
        return WsHealth(
            connected=False,
            subscriber_count=0,
            subscribed_tokens=0,
            last_tick_at=None,
            tick_age_seconds=None,
            tick_count_today=0,
        )
    snap = mux.health_snapshot()
    return WsHealth(**snap)
```

`KiteWsMultiplexer` gains:
- `last_tick_at: datetime | None` attribute, set on every tick callback (~µs cost).
- `tick_count_today: int` counter, reset at IST midnight by a tiny scheduled job that's already in `scheduler.py`.
- `health_snapshot() -> dict` method returning the 6 fields. Calling this is read-only and thread-safe (atomic reads of int + Optional[datetime]).

`ws_registry.get_multiplexer_if_exists(user_id)` is new — mirrors `get_or_create_multiplexer` but skips creation. Returns `None` if no entry.

### 3.5 Frontend — `LiveWsHealthDot.tsx`

```tsx
type Status = "green" | "amber" | "red";

function statusFromAge(age: number | null, connected: boolean): Status {
  if (!connected) return "red";
  if (age == null) return "amber";  // connected but no ticks yet
  if (age < 30) return "green";
  if (age < 120) return "amber";
  return "red";
}
```

- 8px round dot, `bg-green-500` / `bg-amber-500` / `bg-red-500`.
- Hover tooltip: "Connected. Last tick 4s ago. 3 strategies subscribed (12 tokens). 28,471 ticks today."
- Mounted in `PaperTab.tsx` Live segment header, right of "Live mode" label.
- SWR key `ws-health`, refresh interval 10s, `revalidateOnFocus: false`.

### 3.6 Frontend — `KitePostbackPanel.tsx`

- Mounted in the Live segment, below `LiveLandedOrdersList`.
- Calls `GET /v1/algo/live/postbacks?limit=50` (new endpoint that queries `algo.events WHERE type='kite_postback_received' AND user_id=<self>` ordered by `event_ts DESC`).
- Renders table: timestamp · symbol · status · filled_qty · avg_price · raw payload toggle (collapsed JSON).
- Empty state: "No postbacks received. Either no live orders placed today, postbacks not yet enabled (KITE_POSTBACK_ENABLED), or ngrok tunnel down — check `http://localhost:4040`."
- SWR refresh 30s.

---

## 4. Data layer

### 4.1 No new tables

All postback data lands in the existing `algo.events` Iceberg table (`mode='live'`, `type='kite_postback_received'`). Health snapshot is in-memory only.

### 4.2 Indexed lookups

- **Dedup** on `payload->>'guid'` — DuckDB read against Iceberg, ~5-15ms for our volume (≤200 postbacks/day × ~30 days = ~6k rows in the search window). Acceptable.
- **Postback panel** read filters by `user_id` + `type` + last 50 — same DuckDB path.

If volume materially grows we can add a PG-side `algo.kite_postbacks_seen` table with `guid PRIMARY KEY` for O(1) dedup. Out of scope for this spec.

### 4.3 Cache invalidation

Postback handler invalidates `cache:algo:postbacks:{user_id}` after every successful persist (matches CLAUDE.md §5.13 pattern).

---

## 5. Slice decomposition

3 slices, all on `feature/algo-trading-v2-integration` (small enough to pile on the existing branch — no need to spin a new integration branch).

```
Slice OBS-1: WS health + status dot       (3 SP) — independent
Slice OBS-2: Kite postback backend       (5 SP) — depends on OBS-3 for tunnel
Slice OBS-3: ngrok service + docs runbook (2 SP) — independent
Slice OBS-4: Postback panel frontend      (3 SP) — depends on OBS-2
```

Total: **13 SP across ~1.5 sessions.**

Suggested order for today:
1. OBS-1 (WS health) — finish before lunch; gives Mon 09:15 IST smoke a glance-able verdict.
2. OBS-3 (ngrok service) — quick infra task, confirms tunnel works against a curl.
3. OBS-2 (postback backend) — the meat; ends with a manual Dry-run test order producing a real postback event.
4. OBS-4 (postback panel) — finish for visual confirmation.

---

## 6. Testing

### 6.1 Per slice

| Slice | Tests |
|---|---|
| OBS-1 | `test_ws_health_no_mux` (returns `connected=false`); `test_ws_health_snapshot` (with mocked multiplexer); `test_ws_age_seconds` (boundary at 30s/120s); E2E: dot is amber-then-green when a Live run starts with mocked tick source |
| OBS-2 | `test_verify_checksum_pass` (Kite docs sample); `test_verify_checksum_fail`; `test_postback_dedup` (same `guid` twice → second returns `deduplicated=true`); `test_postback_no_secret` (returns 503); `test_postback_resolves_kite_user_id`; `test_postback_persists_event`; integration test with `responses` lib mocking the `kite.orders` correlation |
| OBS-3 | `test_docker_compose_validates` (lint compose file); manual: `curl https://<NGROK_DOMAIN>/v1/health` reaches backend (already-existing health route) |
| OBS-4 | Component test: empty state renders; populated state renders 50 rows; payload toggle expands/collapses; E2E: panel updates within 30s after seeded postback row |

### 6.2 Manual smoke (Mon 09:15 IST + first live trade)

1. Backend up, ngrok up, Kite postback URL configured.
2. Open Live tab → status dot green within 30s of WS connect.
3. Place tiny Dry-run order (₹100, 1 share) → verify in <2s:
   - Postback event appears in `KitePostbackPanel`.
   - `http://localhost:4040` shows the inbound POST with 200 response.
   - Reconciliation panel doesn't flag drift.

---

## 7. Rollout

1. Merge OBS-1, OBS-3, OBS-2, OBS-4 to `feature/algo-trading-v2-integration` via squash PRs (numbers continue from #173).
2. PROGRESS.md updated.
3. CHANGELOG 0.17.0 extended.
4. mkdocs build verified.
5. Open final integration → `dev` PR (rolls in the entire v2 epic + 4 new PRs).
6. After dev merge, configure ngrok + Kite Developer Console for the user's account; flip `KITE_POSTBACK_ENABLED=true`.

---

## 8. Open questions

| Topic | Resolution |
|---|---|
| Should we deprecate the existing `kite.orders()` polling once postbacks land? | **Not yet.** Keep both; postbacks fire-and-forget, polling is the safety net for the first 30 days. Revisit once we have data on lost postback rate. |
| Multi-user Kite postback URL routing (the URL is per-app, not per-user) | The handler already resolves Kite `user_id` → our `user.id` via `auth.broker_credentials`. All 5 users share the same postback URL; payloads are routed by `user_id` field. |
| What if the Zerodha test environment doesn't fire postbacks? | Documented in the runbook: test against a real account with a tiny ₹100 order in Dry-run mode. Kite test environment historically has been spotty for postbacks. |
| ngrok 2-hour session reconnect | The `ngrok` container restarts auto-reconnect. Same `*.ngrok-free.dev` domain persists across reconnects (free tier). No URL change in Kite console needed. |
| Cloudflare Tunnel migration | Out of scope. Documented as the production handoff path in `docs/algo-trading/postbacks.md`. |

---

## 9. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Postback handler down → silent state drift | M | V2-3 reconciliation runs every 5min; postbacks are advisory. Worst case: 5min latency on UI vs broker. |
| Checksum mismatch from `order_timestamp` reformatting | M | Spec explicitly hashes verbatim string; `KitePostbackPayload` model uses `str` not `datetime`. Test `test_verify_checksum_pass` uses Kite docs sample to catch any reformatting bug. |
| Replay attack with old postback payload | L | Idempotency dedup on `guid` already protects against intentional replay. Postback secret is per-app and lives in Keychain (BYO_SECRET_KEY pattern from V2-0). |
| ngrok free tier rate limits hit | L | <200 postbacks/day expected; free tier allows 20k req/month — 100× headroom. |
| Wrong Kite user → wrong account event | L | Lookup via `auth.broker_credentials.kite_client_id` (set during OAuth); fails closed if mapping missing (logs warning, persists event with `our_user_id=null` for forensics). |
| Postback handler exposed to internet without auth | L | Auth IS the checksum. Rate-limited via existing `slowapi`. URL is unguessable (random ngrok subdomain). |
| WS health endpoint creates spurious multiplexer connections | L | `get_multiplexer_if_exists` deliberately doesn't create. Endpoint returns `connected=false` if none. |
| Status dot polls 10s → backend load | L | 5 users × 1 dot × 10s = 0.5 req/s. Endpoint is in-memory dict lookup, ~0.1ms. |

---

## 10. Future work

- **Cloudflare Tunnel** for prod — when graduating from dev. Documented but not built.
- **Postback-driven order state machine** — once postbacks land we can replace the live runtime's `kite.orders()` polling with a state machine that consumes postback events. Cleanup task post-30-day soak.
- **Per-second tick metrics** — `tick_count_today` is daily; a sliding 1-min/5-min/1-hour rate would be a nicer UI. Hard requires a small ring buffer in the multiplexer. Defer to v3.
- **WS health alert email** — if `connected=false` for >5min during market hours, fire an email like the V2-3 drift gate. Defer; user can see the red dot.
