# Kite Postback URL + ngrok Dev Tunnel Research (2026-05-10)

> **Source:** general-purpose subagent + Kite Connect docs scrape, 2026-05-10.
> Anchor for the v2 observability + postback spec.

## Part A — Kite Connect v3 Postback

### A1. Payload schema (key fields)

Kite POSTs raw JSON (`Content-Type: application/json`) to the registered URL. Read raw body and decode yourself.

Most relevant fields:

| Field | Type | Notes |
|---|---|---|
| `user_id` | string | Zerodha client whose order this is |
| `order_id` | string | Kite-side unique order ID |
| `exchange_order_id` | string \| null | `null` if rejected pre-OMS |
| `status` | string | `COMPLETE` \| `REJECTED` \| `CANCELLED` \| `UPDATE` |
| `status_message` | string \| null | Human-readable |
| `tradingsymbol` | string | NSE/BSE symbol |
| `instrument_token` | uint32 | Numeric instrument id |
| `exchange` | string | NSE/BSE/NFO/MCX |
| `transaction_type` | string | BUY \| SELL |
| `order_type` | string | MARKET \| LIMIT \| SL \| SL-M |
| `product` | string | CNC \| MIS \| NRML \| MTF |
| `quantity`, `filled_quantity`, `unfilled_quantity`, `cancelled_quantity` | int64 | |
| `price`, `trigger_price`, `average_price` | float64 | `average_price` set on COMPLETE only |
| `order_timestamp` | string | `"YYYY-MM-DD HH:MM:SS"` IST, **no TZ suffix** |
| `checksum` | string | SHA-256 hex; verify per A2 |
| `tag` | string \| null | What we sent on `place_order` (≤20 alnum) |
| `guid` | string | Unique-per-postback id — **use for idempotency** |

### A2. Signature verification

- **Algorithm:** SHA-256 hex digest, lowercase
- **Hashed string:** `order_id + order_timestamp + api_secret` — verbatim concat, no separator
- **Where it lands:** top-level `checksum` field
- It is **NOT HMAC** — it's plain SHA-256 with secret mixed in. Still use `hmac.compare_digest` for constant-time compare.
- Hash the **exact `order_timestamp` string Kite sent**; do NOT reformat or convert to UTC.

```python
import hashlib
import hmac
import json
from fastapi import APIRouter, Request, HTTPException

def verify_kite_postback(payload: dict, api_secret: str) -> bool:
    """SHA-256(order_id + order_timestamp + api_secret) == checksum."""
    order_id = payload.get("order_id", "")
    order_ts = payload.get("order_timestamp", "")
    expected = hashlib.sha256(
        f"{order_id}{order_ts}{api_secret}".encode("utf-8")
    ).hexdigest()
    received = (payload.get("checksum") or "").lower()
    return hmac.compare_digest(expected, received)
```

### A3. Event types

| `status` | When |
|---|---|
| `COMPLETE` | Fully executed |
| `REJECTED` | RMS / OMS / exchange reject |
| `CANCELLED` | User-cancelled or auto-cancelled (DAY expiry, freeze) |
| `UPDATE` | Open-order modified, partial fill, trigger-pending → open |

There is **no separate OPEN postback** — the `place_order` REST response IS the placed event. UPDATE covers partial fills (multiple UPDATEs with rising `filled_quantity` before final COMPLETE).

### A4. Retry & idempotency

- **No documented automatic retries on 5xx** — postbacks are fire-and-forget. If endpoint is down, event lost.
- **Mitigation:** reconcile against `GET /orders` at startup + on a cron. Treat postbacks as fast path, not source of truth.
- **Timeout:** unspecified; keep handler under ~3s — ack fast, do real work in background task.
- **Idempotency:** dedup on `guid` (or `(order_id, status, exchange_update_timestamp)` composite). Same status update can re-deliver theoretically; multiple distinct UPDATEs are normal each with different `guid`.

### A5. Configuration

- Set in Kite Developer Console (`developers.kite.trade` → app → "Postback URL").
- **Per-app, NOT per-user.** One URL serves all users authenticated against the app.
- **Cannot be updated programmatically.** Manual edit only.
- Console probes the URL on save: must return 2xx on port 80/443.

### A6. HTTPS requirement

- HTTPS required. `http://`, `localhost`, private IPs rejected.
- **Port must be 80 or 443** at registration time.
- **Cert validation:** Kite validates the chain; self-signed fails. Use real CA (Let's Encrypt) or terminate TLS via tunnel (ngrok / Cloudflare).

## Part B — ngrok & tunnel options

### B1. ngrok free tier (May 2026)

| Limit | Free |
|---|---|
| Concurrent endpoints | 3 |
| Bandwidth | 1 GB/month |
| HTTP requests | 20,000/month |
| Session duration | 2-hour reconnect cap |
| Static dev domain | **1 free** `*.ngrok-free.dev` |
| Browser interstitial | Yes (browser visitors only — irrelevant for Kite postbacks) |
| Custom domain | Paid only |

Bandwidth + req-count are non-issues for Kite postbacks (<200/day even on heavy days). The 2-hour session reconnect is mitigated by running ngrok as a long-lived service that auto-reconnects on the same dev domain.

### B2. Recommended path

- **Dev:** ngrok free + 1 dev domain (zero-config, $0).
- **Staging/prod:** Cloudflare Tunnel on existing real domain (free, no bandwidth cap, no interstitial, real TLS, your subdomain). Migrate when graduating from dev-Kite.
- Alternatives noted (frp, sish, localtunnel, Tailscale Funnel) but ngrok wins for ease + Cloudflare wins for prod.

### B3. docker-compose pattern (recommended)

```yaml
services:
  ngrok:
    image: ngrok/ngrok:latest
    restart: unless-stopped
    command:
      - "http"
      - "--domain=${NGROK_DOMAIN}"          # e.g. abhay-aiagent.ngrok-free.dev
      - "backend:8181"
    environment:
      NGROK_AUTHTOKEN: ${NGROK_AUTHTOKEN}
    ports:
      - "4040:4040"                         # inspection UI
    depends_on:
      - backend
```

Setup:
1. Sign up at ngrok.com → copy auth token.
2. Dashboard → Domains → claim free `*.ngrok-free.dev`.
3. Add to `.env`.
4. `docker compose up -d ngrok`.
5. Kite Developer Console → set Postback URL to `https://<dev-domain>/v1/webhooks/kite/postback`.
6. Inspect at `http://localhost:4040` (request inspector — invaluable for debugging checksum mismatches).

### B4. Production handoff

In prod, point Kite postback at real domain (`https://api.<your-domain>/v1/webhooks/kite/postback`), terminate TLS at existing reverse proxy. No tunnel needed. Manual one-time URL edit in Kite console before flipping prod feature flag.

## Sources

- [Postbacks / WebHooks — Kite Connect 3 docs](https://kite.trade/docs/connect/v3/postbacks/)
- [Postback URL not working — Kite forum (port 80/443 requirement)](https://kite.trade/forum/discussion/7704/postback-url-not-working)
- [KiteConnect postback example — ajinasokan gist](https://gist.github.com/ajinasokan/267d68c9f61e3e4ea11681c0ec4e707d)
- [ngrok Free Plan Limits](https://ngrok.com/docs/pricing-limits/free-plan-limits)
- [Static dev domains for all ngrok users](https://ngrok.com/blog/free-static-domains-ngrok-users)
- [Cloudflare Tunnel walkthrough 2026](https://recca0120.github.io/en/2026/04/14/cloudflare-tunnel-2026/)
