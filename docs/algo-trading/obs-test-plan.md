# OBS-1 → OBS-4 Manual Test Plan

> **Purpose:** End-to-end verification of the v2 observability + Kite postback follow-ups before the integration → `dev` PR.
>
> **Estimated time:** 30–45 min for happy paths; +15 min for failure-mode probes.
>
> **Prereqs:** Backend stack up (`docker compose up -d`), pro/superuser cookie, Kite OAuth done (for OBS-1 multiplexer + OBS-2 postback).

---

## Section A — One-time setup (do this BEFORE any test)

### A1. ngrok signup + free domain claim (~5 min)

1. Sign up at https://ngrok.com (free).
2. Dashboard → **Your Authtoken** → copy the long string.
3. Dashboard → **Domains** → **+ Create Domain** → pick a free `*.ngrok-free.dev` slug (e.g. `abhay-aiagent.ngrok-free.dev`). This is **persistent** — survives container restarts and stays the same forever on the free tier.

### A2. Populate `.env`

Edit `/Users/abhay/Documents/projects/ai-agent-ui/.env` (NOT `.env.example`) — add at the bottom:

```
NGROK_AUTHTOKEN=2x...your-long-token-here...
NGROK_DOMAIN=abhay-aiagent.ngrok-free.dev
KITE_POSTBACK_ENABLED=false
```

Leave `KITE_POSTBACK_ENABLED=false` for now — flip to `true` after Kite Developer Console is configured (test step C2).

### A3. Bring up ngrok

```bash
docker compose --profile live up -d ngrok
docker compose --profile live ps ngrok      # state must be "running"
```

Verify:
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://$NGROK_DOMAIN/v1/health
```
Expected: `200`. (If 502, backend is down. If 401/403 hit a public health route or check that backend is bound to `0.0.0.0:8181`.)

Open http://localhost:4040 in a browser — that's ngrok's request inspector. Leave open in a tab; you'll watch postbacks land here in real time during section C.

### A4. Restart backend (CLAUDE.md §6.2 — new routes)

```bash
docker compose restart backend
sleep 5     # asyncpg shutdown race per CLAUDE.md §6.2
```

---

## Section B — OBS-1: WS Health Endpoint + Status Dot

### B1. Endpoint sanity (no live run yet)

```bash
JAR=$(mktemp)
curl -s -c "$JAR" -b "$JAR" \
  -X POST http://localhost:8181/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "<your-pro-or-superuser-email>", "password": "<password>"}' \
  | jq .

curl -s -b "$JAR" http://localhost:8181/v1/algo/live/ws-health | jq .
```

Expected (no active multiplexer):
```json
{
  "connected": false,
  "subscriber_count": 0,
  "subscribed_tokens": 0,
  "last_tick_at": null,
  "tick_age_seconds": null,
  "tick_count_today": 0
}
```

✅ **Pass criteria:** 200, `connected: false`, all numeric fields 0, ISO field null.
❌ **Fail signals:** 500 → check `docker compose logs -f backend | grep ws-health`. 403 → user isn't pro/superuser.

### B2. UI dot — disconnected state

1. Open http://localhost:3000 → log in as pro/superuser.
2. Navigate to **Trading** tab.
3. Toggle **Live** segment.
4. Look at the segment header.

✅ **Pass criteria:** Red 8px dot visible. Hover tooltip says something like "Disconnected" or "Last tick: never". Page does NOT make a Kite WS connection (verify via `docker compose logs -f backend | grep "kite_ws"` — no new connect lines).

### B3. UI dot — green when connected

Start a real live run (you need Kite OAuth done + a saved strategy + caps):

1. Trading tab → **Live** segment → ensure the 4 gates are green (caps set, walkforward < 30d, Kite connected, kill disarmed).
2. Click **Start Run**.
3. Watch the dot in the segment header for ~30s.

✅ **Pass criteria:**
- Dot turns **amber** within 5s (connected, no ticks yet).
- Dot turns **green** within 30s of first tick.
- Hover tooltip shows: "Connected. Last tick Ns ago. M strategies subscribed (T tokens). C ticks today."
- `tick_age_seconds` keeps refreshing every 10s without manual page refresh.

### B4. UI dot — amber on stale ticks

Easiest way to test without waiting for market hours: open a Live run during market open, then disconnect your network for ~2 min:

```bash
# Force a fake stale state: set tick_count_today to 1 manually, no new ticks
# (only doable via SQL/Redis — usually skipped; B3 covers the main path)
```

✅ **Pass criteria:** Dot transitions green → amber after 30s of no new ticks → red after 120s.
❌ **Fail signals:** Dot stuck on green forever (tick_age computation broken). Or stuck red even with active ticks (multiplexer not exposing `last_tick_at`).

### B5. Failure-mode probe — endpoint stays read-only

```bash
# Before any live run, query 10 times
for i in {1..10}; do curl -s -b "$JAR" http://localhost:8181/v1/algo/live/ws-health > /dev/null; done

docker compose logs --tail=50 backend | grep -i "kite_ws\|multiplexer"
```

✅ **Pass criteria:** ZERO `kite_ws` connect logs. The endpoint must NOT spawn a multiplexer as a side-effect of polling — that's the `get_multiplexer_if_exists` design.

---

## Section C — OBS-2: Kite Postback Backend

### C1. Endpoint sanity — fail-closed branches

```bash
# (a) Postback disabled → 503
curl -s -X POST http://localhost:8181/v1/webhooks/kite/postback \
  -H 'Content-Type: application/json' -d '{}' \
  -w '\nHTTP %{http_code}\n'
```
Expected: `HTTP 503` with body like `{"detail":"kite postback disabled"}` or similar.

```bash
# (b) Flip on but bad checksum → 401
# In .env: KITE_POSTBACK_ENABLED=true ; restart backend ; redo
docker compose restart backend && sleep 5

curl -s -X POST http://localhost:8181/v1/webhooks/kite/postback \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"123","order_timestamp":"2026-05-12 09:30:00","checksum":"deadbeef","guid":"abc","status":"COMPLETE","tradingsymbol":"RELIANCE","user_id":"AB1234"}' \
  -w '\nHTTP %{http_code}\n'
```
Expected: `HTTP 401` (bad checksum).

```bash
# (c) Missing guid → 400
# Same as above but drop "guid" field
```
Expected: `HTTP 400`.

```bash
# (d) Invalid JSON → 400
curl -s -X POST http://localhost:8181/v1/webhooks/kite/postback \
  -H 'Content-Type: application/json' -d 'this is not json' \
  -w '\nHTTP %{http_code}\n'
```
Expected: `HTTP 400`.

### C2. Configure postback in Kite Developer Console (~3 min)

1. Open https://developers.kite.trade → log in → your app.
2. Field: **Postback URL** → paste:
   ```
   https://abhay-aiagent.ngrok-free.dev/v1/webhooks/kite/postback
   ```
   (Replace with your actual ngrok domain.)
3. Click **Save**. The console will probe the URL — must return 2xx within ~5s. If it fails, check:
   - `KITE_POSTBACK_ENABLED=true` in `.env`?
   - Backend restarted after `.env` change?
   - ngrok inspector at http://localhost:4040 shows the inbound POST?
   - HTTPS only — `http://...` is rejected by Kite.

### C3. End-to-end happy path — real Kite postback

You need a Kite account in **Dry-run** mode (so no real money moves). Follow the v2 dry-run setup if you haven't already.

1. Trading tab → **Dry run** segment → start a tiny test order (e.g. 1 share of any stock at LIMIT ₹0.05 below close — guaranteed not to fill, then cancel).
2. Within 2 seconds, check:
   - **ngrok inspector** (http://localhost:4040) — should show inbound `POST /v1/webhooks/kite/postback` with 200 response.
   - **Backend logs**: `docker compose logs --tail=20 backend | grep "kite postback"` shows the parsed event.
   - **Postback panel** (UI, OBS-4 — covered in section E) shows the new row.

### C4. Idempotency — same `guid` twice

Manually replay an inbound postback from the ngrok inspector:
1. http://localhost:4040 → click the recent postback request → **Replay** button.
2. Watch backend log for the second handling.

Expected:
- First request: `200 {"ok": true}`.
- Second (replay): `200 {"ok": true, "deduplicated": true}`.

✅ **Pass criteria:** `algo.events` table has exactly ONE row for that `guid` (verify via DuckDB):

```bash
docker compose exec backend python -c "
import duckdb, sys
sys.path.insert(0, '/app')
from backend.algo.iceberg import duckdb_view
con = duckdb_view('algo.events')
print(con.execute(\"SELECT COUNT(*) FROM events WHERE payload->>'guid' = 'YOUR_GUID' AND type='kite_postback_received'\").fetchall())
"
```
Expected: `[(1,)]`.

### C5. Companion read endpoint

```bash
curl -s -b "$JAR" 'http://localhost:8181/v1/algo/live/postbacks?limit=10' | jq '. | length'
```

Expected: integer (likely 1 if you ran C3, more if you've poked C1/C2).

---

## Section D — OBS-3: ngrok Service

### D1. Profile gating

```bash
docker compose config | grep -c "ngrok:" && echo "❌ ngrok in default profile" || echo "✓ ngrok hidden in default"
```
Expected: `✓ ngrok hidden in default` (zero matches).

```bash
docker compose --profile live config | grep -c "ngrok:" && echo "✓ ngrok present in live profile"
```
Expected: `✓ ngrok present in live profile` (one match).

### D2. ngrok survives backend restart

```bash
docker compose restart backend && sleep 5
docker compose --profile live ps ngrok      # ngrok state still "running"
curl -s -o /dev/null -w '%{http_code}\n' https://$NGROK_DOMAIN/v1/health
```
Expected: `200` (ngrok auto-reconnected, same domain — no Kite Developer Console URL update needed).

### D3. Inspector reachable

```bash
curl -s http://localhost:4040 | grep -c "ngrok" && echo "✓ inspector up"
```

---

## Section E — OBS-4: Kite Postback Panel (UI)

### E1. Empty state

After ngrok is up but BEFORE any postback fires:

1. Trading tab → **Live** segment.
2. Scroll down to "Kite Postbacks" panel.

✅ **Pass criteria:** Amber-bordered card with text:
> "No postbacks received. Either no live orders today, postbacks not yet enabled (`KITE_POSTBACK_ENABLED`), or ngrok tunnel down — check `http://localhost:4040`."

### E2. Populated state

After running C3 (real postback):

1. Wait up to 30s (panel SWR refresh).
2. Panel should show the postback row: timestamp · symbol · status badge · filled qty · avg price · ▸ payload toggle.
3. Click ▸ — the raw JSON payload should expand inline (single row at a time; clicking a different row collapses the previous).
4. Status badge color matches: COMPLETE green / REJECTED red / CANCELLED gray / UPDATE blue.

### E3. Mode-gating

1. Toggle **Paper** segment. Panel should be **hidden** entirely.
2. Toggle **Dry run** segment. Panel should be **hidden** entirely.
3. Toggle **Live** segment. Panel reappears.

### E4. Loading + skeleton

Hard-refresh page. Panel should show skeleton rows WITH visible text (e.g. shimmer block placeholders, NOT empty divs — Lighthouse FCP heuristic per CLAUDE.md §5.3).

---

## Section F — Multi-provider ngrok strategy (Razorpay + Stripe + Kite)

**Yes, one ngrok URL serves all webhook providers.** ngrok exposes one backend port at one domain; FastAPI routes by path. Confirmed our payment webhook secrets exist (`razorpay_webhook_secret`, `stripe_webhook_secret` in `backend/config.py`) but no handlers wired yet — clean slate to standardize.

### F1. Recommended path convention

| Provider | Webhook URL |
|---|---|
| Kite (OBS-2) | `https://$NGROK_DOMAIN/v1/webhooks/kite/postback` |
| Razorpay (planned) | `https://$NGROK_DOMAIN/v1/webhooks/razorpay` |
| Stripe (planned) | `https://$NGROK_DOMAIN/v1/webhooks/stripe` |

ngrok config: NO change needed. Same `--domain=$NGROK_DOMAIN backend:8181` command serves all three.

### F2. Free-tier headroom

ngrok free tier: 20,000 req/month + 1 GB bandwidth.
- Kite postbacks: ≤200/day → ~6,000/month (well under).
- Razorpay webhooks: ~10/month (subscription events) → trivial.
- Stripe webhooks: ~30/month (subscription + payment events) → trivial.
- Total well within free-tier headroom (>3× under cap).

### F3. Provider console URLs (when wiring Razorpay + Stripe handlers later)

- **Razorpay Dashboard** → Settings → Webhooks → Add → URL `https://$NGROK_DOMAIN/v1/webhooks/razorpay` → secret = `RAZORPAY_WEBHOOK_SECRET` env var.
- **Stripe Dashboard** → Developers → Webhooks → Add endpoint → URL `https://$NGROK_DOMAIN/v1/webhooks/stripe` → secret = `STRIPE_WEBHOOK_SECRET` env var.

### F4. Production handoff (when ready)

Replace `$NGROK_DOMAIN` with your real domain (e.g. `api.<your-domain>`). Same `/v1/webhooks/<provider>` path convention. One Cloudflare Tunnel instead of ngrok. No code changes — only Developer Console URL edits per provider.

---

## Section G — Sanity at the end

After all tests pass:

```bash
# Confirm integration branch state
git log --oneline -10

# Confirm no lingering test data in events table
docker compose exec backend python -c "
import duckdb, sys
sys.path.insert(0, '/app')
from backend.algo.iceberg import duckdb_view
con = duckdb_view('algo.events')
print(con.execute(\"SELECT COUNT(*) FROM events WHERE type='kite_postback_received' AND DATE(event_ts) = CURRENT_DATE\").fetchall())
"

# (Optional) flip postback off again until you actually trade live
# .env: KITE_POSTBACK_ENABLED=false ; restart backend
```

---

## Troubleshooting cheat-sheet

| Symptom | Likely cause | Fix |
|---|---|---|
| `502 Bad Gateway` from ngrok | Backend down | `docker compose restart backend` |
| Kite console "URL not reachable" | `KITE_POSTBACK_ENABLED=false` or backend not restarted | Flip + restart |
| Checksum 401 in inspector | `kite_api_secret` mismatch | Verify Keychain has correct secret per `BYO_SECRET_KEY` Keychain pattern |
| Status dot stuck red despite active live run | Multiplexer crashed or `health_snapshot` returning stale data | `docker compose logs --tail=100 backend \| grep -E "ws_disconnected\|on_close"` |
| Postback panel empty after C3 | SWR not yet refreshed | Wait 30s OR hard-refresh page |
| ngrok 2-hour reconnect → Kite postback URL still works | Free-tier dev domain is **persistent** by design — no action needed | — |
| Multiple ngrok endpoints (3 limit hit) | You shouldn't need more than 1 — same URL serves all webhook providers | See section F |
