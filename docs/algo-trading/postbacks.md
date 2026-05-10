# Kite Postback URL + ngrok Dev Tunnel Runbook

> **For:** Algo Trading v2 live trading observability.  
> **Audience:** Developers and traders setting up live order monitoring.  
> **Last updated:** 2026-05-10

---

## 1. Why ngrok? (Per-app Kite postback URL)

Zerodha's Kite Postback feature is designed to push order status updates (COMPLETE, REJECTED, CANCELLED, UPDATE) to your server immediately — no polling required.

**Key constraints:**
- **One URL per app** — the postback URL is registered in the Kite Developer Console and applies to ALL users authenticated against your app. It is **not per-user** and cannot be updated programmatically (manual console edit only).
- **HTTPS required** — Kite validates the certificate chain; self-signed certs fail.
- **Port 80 or 443 only** — no arbitrary ports.
- **Must survive backend restarts** — the same URL must consistently reach your backend, even across container restarts and `docker compose up -d` cycles.

**Traditional dev approach:** Restart your laptop's ngrok tunnel → manually update Kite console URL. Tedious and error-prone when testing.

**This spec's approach:** Run ngrok as a long-lived docker-compose service on a claimed free domain (`*.ngrok-free.dev`). Register that domain ONCE in the Kite Developer Console and never touch it again. The domain persists across container restarts and backend code changes — only changes when you explicitly reclaim it or delete the ngrok account.

Cost: $0 (free tier).

---

## 2. One-Time Setup (First Time Only)

### 2.1 Sign up for ngrok

1. Visit [ngrok.com](https://ngrok.com) and create a free account.
2. Verify your email.
3. Navigate to [https://dashboard.ngrok.com](https://dashboard.ngrok.com).

### 2.2 Claim your free static domain

1. In the ngrok Dashboard, click **Domains** (left sidebar).
2. Click **Create Domain**.
3. The system assigns you one free `*.ngrok-free.dev` domain (e.g., `abhay-aiagent.ngrok-free.dev`). Note it.
4. Optionally customize the subdomain name to something memorable.

### 2.3 Copy your auth token

1. In the ngrok Dashboard, click **Your Authtoken** (left sidebar, under "Getting Started").
2. Copy the token (hidden; click the eye icon to reveal).
3. Paste it into your `.env` file:

```
NGROK_AUTHTOKEN=<paste-here>
```

### 2.4 Add your domain to `.env`

```
NGROK_DOMAIN=<your-claimed-domain>.ngrok-free.dev
```

Example:
```
NGROK_AUTHTOKEN=2_3nXk2K...abcd...
NGROK_DOMAIN=abhay-aiagent.ngrok-free.dev
KITE_POSTBACK_ENABLED=false
```

### 2.5 Verify `.env` is loaded (Docker Compose will read it)

```bash
grep NGROK .env
# Should print both NGROK_AUTHTOKEN and NGROK_DOMAIN with values
```

---

## 3. Bringing Up the Tunnel

### 3.1 Start the ngrok service (once, per dev session)

```bash
docker compose --profile live up -d ngrok
```

You should see:
```
[+] Creating 1/1
 ✔ Network ai-agent-ui_default  Created
 ✔ Container ai-agent-ui-backend-1  Running
 ✔ Container ai-agent-ui-ngrok-1    Started
```

### 3.2 Verify ngrok is running

```bash
docker compose ps
```

You should see a row with `ai-agent-ui-ngrok-1` status `Up`.

### 3.3 Check ngrok's inspection UI

Open [http://localhost:4040](http://localhost:4040) in your browser. You should see ngrok's Web Inspector with:
- **Status:** "online"
- **URL:** `https://<your-domain>.ngrok-free.dev`
- Request log (empty until postbacks arrive)

### 3.4 Test the tunnel

```bash
curl https://$(grep NGROK_DOMAIN .env | cut -d= -f2)/v1/health
```

You should get a 200 response with JSON like `{"status": "ok"}` (the existing backend health endpoint).

---

## 4. Registering the URL in Kite Developer Console

### 4.1 Log in to Kite Developer Console

Visit [https://developers.kite.trade](https://developers.kite.trade) and sign in with your Zerodha account.

### 4.2 Select your app

- Click **Apps** (left sidebar).
- Select your trading app (e.g., "AI Agent UI").

### 4.3 Configure the postback URL

1. Scroll to the **Postback URL** section (or click **Settings** → **Webhooks**).
2. Paste your full postback URL:

```
https://<your-domain>.ngrok-free.dev/v1/webhooks/kite/postback
```

Example:
```
https://abhay-aiagent.ngrok-free.dev/v1/webhooks/kite/postback
```

3. Click **Save** or **Update**.

Kite will probe the URL with a test request. Wait for a "URL verified" or similar confirmation message. If it fails:
   - Verify ngrok is running (`docker compose ps`).
   - Test the URL manually in a terminal: `curl https://<domain>/v1/health` should return 200.
   - Check the ngrok inspector at [http://localhost:4040](http://localhost:4040) for any inbound requests.

### 4.4 Enable postbacks in your `.env`

Once the Kite console confirms the URL:

```
KITE_POSTBACK_ENABLED=true
```

Restart the backend to load this config:

```bash
docker compose restart backend
```

---

## 5. Verification Flow (Manual Smoke Test)

### 5.1 Place a tiny test order in Dry-run mode

1. Open the Live tab in the app's Algo Trading section.
2. **In Dry-run mode** (NOT live), place a tiny order:
   - Stock: any (e.g., `INFY.NS`)
   - Quantity: 1
   - Price: ₹1 (low, so it fills quickly)
   - Duration: DAY

3. Click **Place Order**.

Wait 5–10 seconds for the order to fill (or reject).

### 5.2 Check the postback event in the UI

1. Scroll to the **Kite Postbacks** panel (below the live orders list).
2. You should see a new row:
   - **Timestamp:** seconds ago
   - **Symbol:** `INFY` (or your test symbol)
   - **Status:** `COMPLETE` or `REJECTED`
   - **Filled Qty:** `1`
   - **Avg Price:** ₹<price>

If empty, check below.

### 5.3 Inspect the postback in ngrok's Web Inspector

1. Visit [http://localhost:4040](http://localhost:4040).
2. Look for an inbound **POST** request to `/v1/webhooks/kite/postback`.
3. Click the request to see:
   - **Request body:** the full postback JSON (user_id, order_id, status, etc.)
   - **Response:** 200 and `{"ok": true}` from the backend handler

### 5.4 Verify the event landed in the database

(Optional, for deep debugging)

```bash
# SSH into backend container
docker compose exec backend bash

# Query the events table
python -c "
from backend.stocks import get_iceberg_table
import pyarrow as pa

events = get_iceberg_table('algo.events')
scan = events.scan(
    filter=lambda t: (t['type_'] == 'kite_postback_received')
).to_arrow()
print(scan.select(['user_id', 'type_', 'payload']).to_pandas())
"
```

You should see a recent row with `type_='kite_postback_received'` and payload containing your order_id.

---

## 6. Troubleshooting Checklist

### Issue: ngrok container won't start

**Symptom:** `docker compose --profile live up -d ngrok` fails.

**Diagnosis:**
```bash
docker compose --profile live logs ngrok
```

Look for:
- `Failed to read config` — `.env` not found or unparseable.
- `Invalid domain` — `NGROK_DOMAIN` is not a valid ngrok free domain you claimed.
- `Invalid authtoken` — `NGROK_AUTHTOKEN` is malformed or expired.

**Fix:** Verify `.env` has both vars with no typos:
```bash
cat .env | grep NGROK
```

---

### Issue: Kite console rejects the URL on save

**Symptom:** "URL verification failed" in Kite Developer Console.

**Diagnosis:**

1. Verify ngrok is running:
   ```bash
   docker compose ps | grep ngrok
   ```
   Should show `Up`.

2. Verify the tunnel is live:
   ```bash
   curl https://$(grep NGROK_DOMAIN .env | cut -d= -f2)/v1/health
   ```
   Should return 200 with JSON.

3. Check ngrok's inspector for any failed requests:
   ```
   http://localhost:4040
   ```
   Look for requests to `/v1/health` or `/v1/webhooks/kite/postback`. Any 5xx responses?

**Fix:**
- If `curl` hangs, backend or ngrok may be unresponsive. Restart:
  ```bash
  docker compose restart backend ngrok
  ```
- If 5xx in the inspector, check backend logs:
  ```bash
  docker compose logs backend | tail -20
  ```

---

### Issue: 2-hour ngrok session timeout (auto-reconnect)

**Symptom:** Postbacks stop arriving after ~2 hours, then resume.

**Why:** Free tier ngrok sessions reconnect every 2 hours. During reconnect (~5 seconds), the tunnel is briefly unavailable.

**Mitigation:**
- The `restart: unless-stopped` policy in docker-compose automatically reconnects the container.
- The **same domain persists** across reconnects — no URL change needed in Kite console.
- If a postback lands during the 5-second gap, Kite does NOT retry (fire-and-forget semantics). The next reconciliation cron (every 5 min) will catch up.

**You don't need to do anything** — it's automatic.

If you want to monitor reconnects:
```bash
docker compose logs -f ngrok | grep -i reconnect
```

---

### Issue: Postback arrives but no event in the UI / database

**Symptom:** ngrok inspector shows 200 response, but the postback panel is empty.

**Diagnosis:**

1. Check backend logs for the postback handler:
   ```bash
   docker compose logs backend | grep -i "postback"
   ```
   Look for warnings about checksum failure, missing guid, or dedup.

2. Verify `KITE_POSTBACK_ENABLED=true` in `.env`:
   ```bash
   grep KITE_POSTBACK_ENABLED .env
   ```

3. Verify backend was restarted after setting the flag:
   ```bash
   docker compose logs backend | grep -i "starting" | tail -1
   # Check the timestamp is recent
   ```

**Fix:**
- If checksum failed: verify the Kite API secret is correctly loaded. Check backend startup logs for `Secret loaded: algo_kite_api_secret`.
- If guid is missing: the postback is malformed. Inspect the raw body in ngrok inspector (§5.3 step 3) — it should have a `guid` field.
- If backend wasn't restarted: `docker compose restart backend`.

---

### Issue: Free tier domain expired

**Symptom:** `curl https://<domain>/v1/health` returns connection refused after weeks of use.

**Why:** Free tier domains are claimed for 14 days without activity. If the ngrok client stops calling home, the domain is recycled.

**Prevention:** The docker-compose service (`restart: unless-stopped`) keeps the connection alive. Just ensure `docker compose --profile live up -d ngrok` is running continuously or restarted regularly.

**Fix:** If domain is lost, claim a new one and update `.env`:
```bash
# Claim new domain in ngrok Dashboard
# Update .env
NGROK_DOMAIN=<new-domain>.ngrok-free.dev
# Update Kite console postback URL
# Restart ngrok
docker compose restart ngrok
```

---

## 7. Cloudflare Tunnel Migration Path (Prod Handoff)

> **Out of scope for this dev runbook.** Documented for reference when graduating from dev to production.

### When to migrate

Once the app goes live and Kite postbacks must be reliable:
- Free tier ngrok has a 20k req/month quota (sufficient for ~250/day); if you exceed it, postbacks are rate-limited.
- 2-hour session reconnects add minor latency during the window.
- For production, use a **Cloudflare Tunnel** on your real domain.

### High-level path

1. **Cloudflare Tunnel setup** — on your DNS provider, point a subdomain (e.g., `api.yourdomain.com`) to a Cloudflare Tunnel.
2. **Local tunnel connector** — run `cloudflared tunnel` instead of ngrok (same docker-compose pattern, different image + command).
3. **No bandwidth / request caps** — Cloudflare Tunnel is free and unlimited for 1 tunnel.
4. **TLS already included** — Cloudflare handles cert renewal automatically.
5. **One-time Kite console update** — point postback URL to your real domain:
   ```
   https://api.yourdomain.com/v1/webhooks/kite/postback
   ```

### References

- [Cloudflare Tunnel quickstart](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
- [Cloudflare Tunnel with Docker](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/#docker)

For now, stick with ngrok free tier during dev and testing. Migrate to Cloudflare when the app is live.

---

## Acceptance Criteria

- [ ] `.env` contains `NGROK_AUTHTOKEN`, `NGROK_DOMAIN`, `KITE_POSTBACK_ENABLED` with no secrets committed.
- [ ] `docker compose --profile live config` validates without errors.
- [ ] `docker compose config` (default profile) does NOT include ngrok service.
- [ ] `docker compose --profile live up -d ngrok` starts the container (check `docker compose ps`).
- [ ] `http://localhost:4040` is reachable and shows "online" status.
- [ ] `curl https://<NGROK_DOMAIN>/v1/health` returns 200 from backend.
- [ ] `docs/algo-trading/postbacks.md` covers all 7 sections with exact setup steps.
- [ ] `README.md` env-vars table includes NGROK_AUTHTOKEN, NGROK_DOMAIN, KITE_POSTBACK_ENABLED.
- [ ] All commits have `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`.
- [ ] No `NGROK_AUTHTOKEN` or actual domain value committed (only `.env.example` has placeholders).

---

## Verifying postbacks in the UI (OBS-4)

Once the ngrok tunnel is up and the Kite Developer Console postback URL
is configured (see sections above), place a small test order in Dry-run
mode and watch the **Kite postbacks** panel in the Trading tab.

### Panel location

1. Open **Algo Trading → Trading tab**.
2. Ensure the **Live** mode button is selected (top-right toggle).
3. Select any strategy from the "Strategy" dropdown.
4. The **Kite postbacks** panel appears below the "In-flight orders" panel.

### What you should see

| State | Panel appearance |
|---|---|
| No postbacks received today | Amber card with the troubleshooting hint (see §C1 of `obs-test-plan.md`) |
| Postbacks flowing | Table rows: timestamp · symbol · status badge · filled qty · avg price · ▸ |
| Status `COMPLETE` | Green badge |
| Status `REJECTED` | Red badge |
| Status `CANCELLED` | Grey badge |
| Status `UPDATE` | Blue badge |

### Expanding the raw payload

Click the ▸ at the end of any row to expand the raw Kite postback JSON.
Only one row is expanded at a time — clicking another row collapses the
previous one. Useful for diagnosing checksum failures or unexpected
field values.

### SWR refresh cadence

The panel refreshes every **30 seconds**. To force an immediate refresh,
reload the page (Cmd+R). A postback typically appears in the panel
within ≤ 30 s of being received (network round-trip + SWR next tick).
