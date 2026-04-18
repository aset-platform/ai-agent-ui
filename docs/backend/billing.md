# Billing & Subscriptions

Subscription management with Razorpay (INR) payment gateway, tier-based access control, and monthly usage tracking.

---

## Subscription Tiers

| Tier | Price | Analyses/Month | Chat Messages/Day | Features |
|------|-------|---------------|-------------------|----------|
| **Free** | ₹0 | 3 | 10 | Basic stock data, 3-month forecast |
| **Pro** | ₹499/mo | 30 | 100 | All forecast horizons, market news, priority support |
| **Premium** | ₹1,499/mo | Unlimited | Unlimited | All features, early access, dedicated support |

Configuration lives in `backend/subscription_config.py`:

```python
TIER_ORDER = {"free": 0, "pro": 1, "premium": 2}
USAGE_QUOTAS = {"free": 3, "pro": 30, "premium": 0}  # 0 = unlimited
```

### Side effect: role auto-sync

Any time `subscription_tier` is written via
`auth/repo/user_writes.py::update()`, the user's `role` is kept in
sync:

- `free` → `role="general"`
- `pro`, `premium` → `role="pro"`

**Superuser is sticky** — subscription changes never demote a superuser.
Role transitions fire `ROLE_PROMOTED` / `ROLE_DEMOTED` audit events.
Checkout success handlers, webhook handlers (`_handle_charged`,
`_handle_cancelled`), and `POST /subscription/cancel` all flow through
the same helper, so the role is always consistent with the persisted
tier. See `docs/backend/auth.md` for the role model and the
`/admin/*` scope-gate pattern pro users unlock.

---

## Data Model

### User Fields (Iceberg `auth.users`)

| Column | Type | Description |
|--------|------|-------------|
| `subscription_tier` | string | `free`, `pro`, or `premium` |
| `subscription_status` | string | `active`, `cancelled`, `past_due`, `expired` |
| `razorpay_customer_id` | string | Razorpay customer ID |
| `razorpay_subscription_id` | string | Current active subscription ID |
| `stripe_customer_id` | string | Stripe customer ID (future) |
| `stripe_subscription_id` | string | Stripe subscription ID (future) |
| `monthly_usage_count` | int | Analyses used this month |
| `usage_month` | string | Month the counter belongs to (`YYYY-MM`) |
| `subscription_start_at` | timestamp | When subscription started |
| `subscription_end_at` | timestamp | When subscription ends |

### Usage History Table (`auth.usage_history`)

Monthly snapshots archived automatically on reset:

| Column | Type | Description |
|--------|------|-------------|
| `user_id` | string | User UUID |
| `month` | string | `YYYY-MM` |
| `usage_count` | int | Analyses that month |
| `tier` | string | Tier during that month |
| `archived_at` | timestamp | When archived |

---

## API Endpoints

### Subscription Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/v1/subscription/checkout` | User | Create or upgrade subscription |
| `GET` | `/v1/subscription` | User | Current tier, status, usage |
| `POST` | `/v1/subscription/cancel` | User | Cancel active subscription |
| `POST` | `/v1/webhooks/razorpay` | None | Razorpay webhook handler |
| `POST` | `/v1/subscription/cleanup` | Superuser | Triage + cancel orphaned subs |

### Admin Usage Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/v1/admin/usage-stats` | Superuser | All users with usage counts |
| `POST` | `/v1/admin/reset-usage` | Superuser | Zero all users |
| `POST` | `/v1/admin/reset-usage/selected` | Superuser | Zero specific users |
| `GET` | `/v1/admin/usage-history` | Superuser | Month-on-month history |

---

## Razorpay Integration

### Setup (Test Mode)

1. Create account at [dashboard.razorpay.com](https://dashboard.razorpay.com)
2. Toggle **Test Mode** (top-left)
3. **Settings → API Keys → Generate Test Key**
4. **Products → Subscriptions → Plans → Create Plan**:
    - Pro: ₹499/month, period=monthly
    - Premium: ₹1,499/month, period=monthly
5. **Settings → Webhooks → Add New Webhook**:
    - URL: `https://<ngrok-url>/v1/webhooks/razorpay`
    - Events: `subscription.charged`, `subscription.cancelled`, `payment.failed`

### Environment Variables

Add to `~/.ai-agent-ui/backend.env`:

```bash
RAZORPAY_KEY_ID=rzp_test_xxxxx
RAZORPAY_KEY_SECRET=xxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxx          # optional in test mode
RAZORPAY_PLAN_PRO=plan_xxxxx
RAZORPAY_PLAN_PREMIUM=plan_xxxxx
```

### Webhook Testing with ngrok

Razorpay webhooks require a publicly reachable URL. Since `localhost:8181` isn't accessible from the internet, use **ngrok** to create a secure tunnel.

#### Initial Setup (one-time)

```bash
# 1. Install ngrok
brew install ngrok          # macOS
# or: snap install ngrok    # Linux

# 2. Create free account at https://ngrok.com and copy auth token

# 3. Configure auth token
ngrok config add-authtoken YOUR_AUTH_TOKEN_HERE
```

#### Starting the Tunnel

```bash
# Start tunnel pointing to backend port
ngrok http 8181

# Output shows your public URL:
# https://abc123.ngrok-free.app → http://localhost:8181
```

Your webhook URL will be:
```
https://abc123.ngrok-free.app/v1/webhooks/razorpay
```

#### ngrok Inspector (Debugging)

Open **http://127.0.0.1:4040** in your browser to:

- See all incoming webhook requests in real-time
- Inspect headers, body, and response for each request
- **Replay** failed requests (click "Replay" button)
- Check response status codes (200 = success, 400 = bad signature, 404 = wrong URL)

#### Configuring Razorpay Webhook

1. Go to **Razorpay Dashboard → Settings → Webhooks → Add New Webhook**
2. Paste your ngrok URL: `https://abc123.ngrok-free.app/v1/webhooks/razorpay`
3. Select events: `subscription.charged`, `subscription.cancelled`, `payment.failed`
4. **Webhook Secret**: Leave empty for test mode (signature verification is skipped), or set a secret for production-like testing
5. Click **Create Webhook**

#### Dev vs QA: Separate Webhook URLs

!!! important "Do NOT share webhook URLs between environments"
    Each environment (dev, QA, staging) has its own backend instance on a different port or machine. Each needs its **own ngrok tunnel** and **own webhook URL** in Razorpay.

**Razorpay supports multiple webhook endpoints** per account. Configure one per environment:

| Environment | Backend | ngrok Command | Razorpay Webhook URL |
|-------------|---------|---------------|---------------------|
| **Dev** (local) | `localhost:8181` | `ngrok http 8181` | `https://dev-xxx.ngrok-free.app/v1/webhooks/razorpay` |
| **QA** | `qa-server:8181` | `ngrok http https://qa-server:8181` | `https://qa-yyy.ngrok-free.app/v1/webhooks/razorpay` |
| **Staging** | `staging:8181` | Not needed if public | `https://staging.yourdomain.com/v1/webhooks/razorpay` |

To add multiple webhooks in Razorpay Dashboard:
1. Go to **Settings → Webhooks**
2. Click **Add New Webhook** for each environment
3. Use a naming convention in the alert email: `dev-webhooks@...`, `qa-webhooks@...`

#### Free Tier Limitations

!!! warning "ngrok free tier URL changes on restart"
    Every time you restart ngrok, you get a **new random URL**. You must update the webhook URL in Razorpay Dashboard each time.

    **Workaround options:**

    1. **Keep ngrok running** — don't restart between test sessions
    2. **ngrok paid plan** — custom static subdomain (e.g., `aset-dev.ngrok.io`)
    3. **Skip webhooks in dev** — test with curl/pytest instead of live Razorpay callbacks (the webhook handler can be called directly)

#### Manual Webhook Testing (without ngrok)

You can test the webhook handler locally without ngrok by calling it directly:

```bash
# Simulate subscription.charged event
curl -X POST http://127.0.0.1:8181/v1/webhooks/razorpay \
  -H "Content-Type: application/json" \
  -d '{
    "event": "subscription.charged",
    "payload": {
      "subscription": {
        "entity": {
          "id": "sub_test123",
          "plan_id": "plan_pro_id",
          "customer_id": "cust_test123",
          "status": "active"
        }
      },
      "payment": {"entity": {}}
    }
  }'

# Expected: {"status": "ok"}
# Check backend logs for: "Tier activated: user_id=..."
```

#### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| **404 Not Found** | Wrong webhook URL path | Use `/v1/webhooks/razorpay` (not `/v1/subscription/webhooks/razorpay`) |
| **400 Bad Request** | Signature verification failed | Set `RAZORPAY_WEBHOOK_SECRET=` (empty) to skip verification in test mode |
| **200 OK but tier not updated** | Webhook sub_id doesn't match user's stored sub_id | Run cleanup from Admin → Maintenance to fix orphaned subs |
| **ngrok shows request but backend has no log** | ngrok free tier shows interstitial page | Add `ngrok http 8181 --host-header=localhost:8181` |
| **Webhook not received at all** | Razorpay retry exhausted or wrong URL | Check Razorpay Dashboard → Webhooks → Recent Deliveries → Retry |

### Test Cards

| Card | Number | Notes |
|------|--------|-------|
| Mastercard (recurring) | `5267 3181 8797 5449` | Supports subscriptions |
| UPI | `success@razorpay` | Auto-succeeds in test mode |

!!! warning
    `4111 1111 1111 1111` does **NOT** support recurring payments in Razorpay test mode.

---

## Checkout Flow

### New Subscription (Free → Pro/Premium)

```
Frontend: Click "Subscribe" on pricing card
  → POST /v1/subscription/checkout {tier: "pro", gateway: "razorpay"}
  → Backend creates Razorpay subscription + customer
  → Returns {subscription_id, key_id, plan_id, upgraded: false}
  → Frontend opens Razorpay checkout.js modal
  → User pays with test card
  → Razorpay sends webhook: subscription.charged
  → Backend updates tier in Iceberg
  → Frontend refreshes token + subscription status
```

### Upgrade (Pro → Premium)

```
Frontend: Click "Upgrade" on Premium card
  → POST /v1/subscription/checkout {tier: "premium"}
  → Backend detects active subscription
  → PATCH /subscriptions/:id {plan_id: premium, schedule_change_at: "now"}
  → Razorpay handles pro-rata billing automatically
  → Returns {subscription_id, ..., upgraded: true}
  → Frontend shows instant success (no modal needed)
  → Token refresh picks up new tier
```

### Cancel

```
Frontend: Click "Cancel plan"
  → POST /v1/subscription/cancel
  → Backend calls Razorpay cancel API
  → Sets tier=free, status=cancelled, clears razorpay_subscription_id
  → Token refresh picks up free tier
```

---

## Access Control

### Guard Dependencies (`auth/dependencies.py`)

```python
from auth.dependencies import require_tier, check_usage_quota

# Require minimum tier
@router.get("/pro-feature")
def pro_feature(user = Depends(require_tier("pro"))):
    ...  # 403 if user tier < pro

# Check monthly quota
@router.post("/analyse")
def analyse(user = Depends(check_usage_quota)):
    ...  # 429 if usage_remaining == 0
```

### JWT Claims

Access tokens include subscription data:

```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "role": "general",
  "subscription_tier": "pro",
  "subscription_status": "active",
  "usage_remaining": 25
}
```

!!! important
    **Always read tier/status from Iceberg** (via `repo.get_by_id()`), not from JWT. The JWT is a stale cache — it only updates on token refresh.

---

## Usage Tracking

### How It Works

1. After each successful chat/analysis, `increment_usage(user_id)` is called
2. It checks `usage_month` on the user record
3. If month differs from current → **archives** old count to `auth.usage_history`, resets to 0
4. Then increments the counter

No cron job needed — resets happen lazily on first API call of a new month.

### Monthly Auto-Reset Flow

```
User sends first message of April:
  → increment_usage() called
  → usage_month == "2026-03" but current is "2026-04"
  → Archive: (user_id, "2026-03", count=12, tier="pro") → usage_history
  → Reset: monthly_usage_count=0, usage_month="2026-04"
  → Increment: monthly_usage_count=1
```

---

## Frontend Components

| Component | Location | Description |
|-----------|----------|-------------|
| `BillingTab` | `components/BillingTab.tsx` | Pricing cards, usage meter, checkout/cancel |
| `UsageBadge` | `components/ChatHeader.tsx` | Compact usage pill (green/yellow/red) |
| `UpgradeBanner` | `components/UpgradeBanner.tsx` | Amber banner when quota exhausted |

### Where Billing is Accessible

- **Profile dropdown → "Billing"** → Opens EditProfileModal on Billing tab
- **EditProfileModal → Billing tab** → Full billing management
- **UpgradeBanner** → Appears below header when quota hits 0, links to Billing tab

---

## Admin Maintenance

The **Admin → Maintenance** tab provides:

### Subscription Cleanup (Triage)

Scans all active Razorpay subscriptions and classifies:

- **Matched** — sub_id matches a user's current subscription → Keep
- **Orphaned** — same customer but different sub_id → Safe to cancel
- **Unlinked** — no user found for that customer_id → Manual review only

Two-phase: "Scan" (read-only) → "Execute Cleanup" (cancels orphaned only).

### Usage Reset

Scan all users with usage stats, then:

- Reset individual user (per-row "Reset" link)
- Reset selected users (checkbox + "Reset Selected")
- Reset all users (with confirmation dialog)

### Data Retention

Same scan + selective pattern for Iceberg table cleanup.

---

## Webhook Events Handled

| Event | Action |
|-------|--------|
| `subscription.charged` | Set tier based on plan_id, status=active |
| `subscription.cancelled` | Set tier=free, status=cancelled, clear sub_id |
| `payment.failed` | Set status=past_due |

### Webhook Guards

- **Signature verification** — HMAC-SHA256 (skipped if `RAZORPAY_WEBHOOK_SECRET` is empty)
- **Stale sub guard** — Ignores events where webhook sub_id ≠ user's stored sub_id
- **Iceberg retry** — `_safe_update()` retries 3 times on `CommitFailedException`

---

## File Reference

| File | Purpose |
|------|---------|
| `backend/subscription_config.py` | Tier constants, quotas, pricing |
| `backend/usage_tracker.py` | increment, reset, archive, history functions |
| `auth/endpoints/subscription_routes.py` | All subscription API endpoints + webhook |
| `auth/dependencies.py` | `require_tier()`, `check_usage_quota()` |
| `auth/repo/schemas.py` | Iceberg schema (user fields + usage_history) |
| `frontend/components/BillingTab.tsx` | Billing UI in EditProfileModal |
| `frontend/components/UpgradeBanner.tsx` | Quota exhaustion banner |
| `frontend/hooks/useAdminData.ts` | `useAdminMaintenance()` hook |
