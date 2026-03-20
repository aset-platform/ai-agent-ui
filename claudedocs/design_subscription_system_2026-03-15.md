# Subscription System Design — AI Agent UI

**Date**: 2026-03-15
**Status**: Draft — pending approval

---

## Architecture Overview

```
                    Frontend (Next.js)
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
     /billing page   Chat UI    Settings
     (subscribe)   (paywall)   (manage)
            │            │            │
            ▼            ▼            ▼
         ┌───────── FastAPI ──────────┐
         │                            │
    Subscription         Chat/Analysis
    Endpoints            Endpoints
         │                    │
         │              SubscriptionGuard
         │              (checks tier+quota)
         │                    │
         ▼                    ▼
    ┌─────────┐        ┌──────────┐
    │Razorpay │        │  Agent   │
    │ Stripe  │        │  Loop    │
    │  APIs   │        │ (tools)  │
    └────┬────┘        └──────────┘
         │
    Webhooks ──→ Update user tier
         │       in Iceberg
         ▼
    ┌──────────────────┐
    │  Iceberg Users   │
    │  (subscription   │
    │   fields added)  │
    └──────────────────┘
```

## Tier Configuration

| Tier | Price (INR) | Price (USD) | Analyses/mo | Chats/day | Forecasts | Compare |
|------|------------|-------------|-------------|-----------|-----------|---------|
| Free | Rs.0 | $0 | 3 | 10 | 3-month only | No |
| Pro | Rs.499/mo | $6/mo | 30 | 100 | 3/6/9 month | 3 stocks |
| Premium | Rs.1,499/mo | $18/mo | Unlimited | Unlimited | All + custom | 10 stocks |

## Data Model Changes

### Iceberg `auth.users` — New Columns

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `subscription_tier` | string | Yes | `"free"` |
| `subscription_status` | string | Yes | `"active"` |
| `subscription_start` | timestamp | Yes | null |
| `subscription_end` | timestamp | Yes | null |
| `stripe_customer_id` | string | Yes | null |
| `stripe_subscription_id` | string | Yes | null |
| `razorpay_customer_id` | string | Yes | null |
| `razorpay_subscription_id` | string | Yes | null |
| `monthly_usage_count` | int64 | No | 0 |

### JWT Access Token — New Claims

```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "role": "general",
  "subscription_tier": "pro",
  "subscription_status": "active",
  "usage_remaining": 27
}
```

## API Endpoints (New)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/v1/subscription` | authenticated | Current tier + usage |
| POST | `/v1/subscription/checkout` | authenticated | Create Razorpay/Stripe checkout |
| POST | `/v1/subscription/cancel` | authenticated | Cancel subscription |
| POST | `/v1/webhooks/razorpay` | webhook secret | Razorpay event handler |
| POST | `/v1/webhooks/stripe` | webhook secret | Stripe event handler |

## Payment Flow

### Razorpay (Primary — INR)

```
User clicks "Upgrade to Pro"
  → Frontend calls POST /v1/subscription/checkout
    { gateway: "razorpay", tier: "pro" }
  → Backend creates Razorpay Subscription
  → Returns subscription_id + key_id
  → Frontend opens Razorpay Checkout modal
  → User pays via UPI/Card/Netbanking
  → Razorpay sends webhook to POST /v1/webhooks/razorpay
    event: subscription.charged
  → Backend updates user: tier=pro, status=active
  → Next JWT refresh includes tier=pro
```

### Stripe (Secondary — International)

```
User clicks "Upgrade to Pro"
  → Frontend calls POST /v1/subscription/checkout
    { gateway: "stripe", tier: "pro" }
  → Backend creates Stripe Checkout Session
  → Returns checkout_url
  → Frontend redirects to Stripe hosted checkout
  → User pays
  → Stripe sends webhook to POST /v1/webhooks/stripe
    event: checkout.session.completed
  → Backend updates user: tier=pro, status=active
```

## Subscription Guard (Middleware)

```python
# New dependency in auth/dependencies.py
def require_tier(
    min_tier: str,
) -> Callable:
    def guard(
        user: UserContext = Depends(get_current_user),
    ) -> UserContext:
        tier_order = {"free": 0, "pro": 1, "premium": 2}
        if tier_order.get(user.subscription_tier, 0) \
           < tier_order[min_tier]:
            raise HTTPException(
                status_code=403,
                detail=f"Requires {min_tier} tier",
            )
        return user
    return guard
```

Applied to chat/analysis endpoints:
- `analyse_stock_price` — checks quota before execution
- `forecast_stock` — Pro+ for 6/9 month horizons
- `search_market_news` — Pro+ only
- General chat — checks daily message limit

## File Structure (New)

```
auth/
├── subscription.py          # SubscriptionService class
├── endpoints/
│   └── subscription_routes.py  # checkout, cancel, status
├── webhooks/
│   ├── razorpay_handler.py  # Razorpay webhook events
│   └── stripe_handler.py    # Stripe webhook events

backend/
├── subscription_config.py   # Tier limits, plan IDs

frontend/
├── app/billing/page.tsx     # Billing/subscription page
├── components/
│   ├── PricingCards.tsx      # Tier comparison cards
│   ├── UsageMeter.tsx       # Usage bar in header
│   └── PaywallModal.tsx     # "Upgrade to Pro" modal
```
