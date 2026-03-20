# Research Report: Paywall Strategy for AI Agent UI

**Date**: 2026-03-15
**Depth**: Exhaustive
**Context**: Stock analysis platform with agentic chat, Prophet forecasting, technical analysis

---

## Executive Summary

For a stock analysis tool with AI-powered chat, the recommended strategy is a **freemium + usage-based hybrid** model with **Stripe** as the payment gateway (free sandbox, best FastAPI/Next.js integration, no upfront cost). The paywall should sit after the user's first successful stock analysis (the "wow moment"), with free tier limited by analysis count, not features.

---

## 1. Paywall Models — What Others Are Doing

### Industry Benchmarks (Stock Analysis Platforms)

| Platform | Free Tier | Paid Tiers | Model |
|----------|-----------|------------|-------|
| **TradingView** | 1 chart, 5 alerts, 7Y data | $14.95–$59.95/mo | Feature-gated |
| **Seeking Alpha** | 1 premium article, watchlist | $239–$2,400/yr | Content-gated |
| **Koyfin** | Basic fundamentals | $39–$179/mo | Data-depth gated |
| **Gainify** | Limited screener | $19–$49/mo | Analysis-count gated |

### AI SaaS Pricing Trends (2026)

- **56% of SaaS** now use some usage-based component (up from 38% in 2023)
- **78%** implement value-based pricing (tied to customer-perceived value)
- Pure per-seat pricing is declining — usage/hybrid is the norm for AI tools
- "Careful Complexity" is the 2026 trend: simple pricing is not always optimal

### Paywall Placement Best Practices

- Paywall should appear **after** the user experiences value (the "wow moment")
- Companies aligning paywalls with natural product limitations see **25% higher conversion** than time-based trials
- Over **80% of SaaS** implement some freemium offering

---

## 2. Recommended Pricing Tiers for AI Agent UI

### Tier Structure

| Tier | Price | Limits | Target |
|------|-------|--------|--------|
| **Free** | $0 | 3 analyses/month, 1 forecast, basic chat | Trial users |
| **Pro** | $19/mo | 30 analyses/month, unlimited forecasts, full chat, comparison reports | Active traders |
| **Premium** | $49/mo | Unlimited analyses, API access, priority queue, custom alerts | Professional |

### What's Gated (Paywall Boundaries)

| Feature | Free | Pro | Premium |
|---------|------|-----|---------|
| Stock analyses per month | 3 | 30 | Unlimited |
| Forecast horizons | 3-month only | 3/6/9 month | 3/6/9 + custom |
| Chat messages per day | 10 | 100 | Unlimited |
| Comparison reports | No | Yes (3 stocks) | Yes (10 stocks) |
| Dashboard access | View only | Full | Full + export |
| News search | No | Yes | Yes |
| API access | No | No | Yes |
| Priority LLM queue | No | No | Yes (synthesis-first) |

### Why This Structure

1. **Free tier = acquisition**: Let users run 3 full analyses to experience the value
2. **Analysis count = natural gate**: Maps directly to LLM API cost (your biggest expense)
3. **$19 Pro sweet spot**: Below TradingView Essential ($14.95), above commodity screeners
4. **Premium at $49**: Justified by unlimited LLM calls + API access

---

## 3. Payment Gateway Comparison (Sandbox Focus)

| Gateway | Sandbox | Signup Cost | Credit Card Required | Best For | Subscription Support | FastAPI/Next.js |
|---------|---------|-------------|---------------------|----------|---------------------|----------------|
| **Stripe** | Full sandbox + test mode | Free | No | Global SaaS | Billing + Checkout | Excellent (stripe-python, @stripe/stripe-js) |
| **Razorpay** | Test mode toggle | Free | No | India-focused | Subscriptions API | Good (razorpay-python) |
| **Paddle** | Sandbox environment | Free | No | MoR (tax handled) | Built-in billing | Webhooks only |
| **Lemon Squeezy** | Test mode | Free | No | Solo devs | Built-in billing | Webhooks + JS SDK |
| **PayPal** | Sandbox accounts | Free | No | Global payments | Recurring billing | REST API |

### Detailed Assessment

#### Stripe (RECOMMENDED)

- **Sandbox**: Full sandbox environment with isolated test data. Test API keys start with `pk_test_` / `sk_test_`. No real charges ever.
- **Test cards**: `4242424242424242` (success), `4000000000000002` (decline), any future expiry, any CVC
- **Subscription billing**: Native `Stripe Billing` with Checkout sessions, Customer Portal, webhooks
- **FastAPI integration**: `stripe` Python SDK, `fastapi-stripe` patterns well-documented
- **Next.js integration**: `@stripe/stripe-js` + `@stripe/react-stripe-js` for embedded checkout
- **Cost**: 2.9% + $0.30 per transaction (only in production)
- **Why best for you**: Already have FastAPI + Next.js. Stripe has the most tutorials/examples for this exact stack. Zero upfront cost.

#### Razorpay (ALTERNATIVE for India)

- **Sandbox**: Toggle "Live/Test" on dashboard. Instant sandbox identical to live.
- **Best for**: If your primary market is India (INR payments, UPI, Indian cards)
- **Cost**: 2% + Rs.3 per domestic transaction
- **Limitation**: International payments have higher fees and compliance overhead

#### Paddle / Lemon Squeezy (SIMPLEST)

- **Merchant of Record**: They handle tax compliance globally — you never deal with GST/VAT
- **Cost**: 5% + $0.50 per transaction (higher, but tax included)
- **Best for**: If you want zero tax/compliance headaches
- **Limitation**: Less control over checkout UX, webhook-only integration

---

## 4. Recommended Architecture (Stripe + Your Stack)

### Integration Points

```
Frontend (Next.js)                    Backend (FastAPI)
─────────────────                    ─────────────────

Stripe Checkout ──→ POST /v1/subscribe ──→ Stripe API
  (hosted page)        │                   (create subscription)
                       │
                       ▼
                  Stripe Webhook ──→ POST /v1/webhooks/stripe
                                        │
                                        ▼
                                   Update user.subscription_tier
                                   in Iceberg (users table)
                                        │
                                        ▼
                                   JWT includes tier claim
                                   { "tier": "pro", ... }
                                        │
                                        ▼
                                   Middleware checks tier
                                   before tool execution
```

### Implementation Steps (Sandbox)

1. **Stripe account** — sign up at stripe.com (free, no credit card)
2. **Test API keys** — get from Dashboard > Developers > API Keys
3. **Products + Prices** — create Pro ($19/mo) and Premium ($49/mo) in Stripe Dashboard (test mode)
4. **Backend**: New `auth/subscription.py` — FastAPI endpoints for checkout session creation and webhook handling
5. **Middleware**: New `SubscriptionGuard` dependency — checks `user.tier` from JWT before allowing analysis tools
6. **Frontend**: Stripe Checkout redirect from settings/billing page
7. **Usage tracking**: Count analyses per user per month in Iceberg `stocks.analysis_summary` (already tracked!)

### What You Already Have (No New Work)

- JWT auth with role-based access (just add `tier` claim)
- User repository in Iceberg (add `subscription_tier`, `stripe_customer_id` columns)
- Analysis count per user already tracked in `stocks.analysis_summary`
- Rate limiting infrastructure (slowapi — extend for tier-based limits)

### New Components Needed

| Component | Location | Effort |
|-----------|----------|--------|
| Stripe checkout endpoints | `auth/endpoints/subscription_routes.py` | 2 SP |
| Stripe webhook handler | `backend/webhooks.py` | 2 SP |
| Subscription guard middleware | `auth/dependencies.py` | 1 SP |
| User tier migration (Iceberg) | `auth/migrate_users_table.py` | 1 SP |
| JWT tier claim | `auth/tokens.py` | 1 SP |
| Frontend billing page | `frontend/app/billing/page.tsx` | 3 SP |
| Usage counter + tier limits | `backend/tools/_ticker_linker.py` | 2 SP |
| Stripe test suite | `tests/backend/test_subscription.py` | 2 SP |
| **Total** | | **14 SP** |

---

## 5. Strategy Recommendation

### Phase 1: Sandbox Integration (Sprint 2-3, 14 SP)

1. Sign up for Stripe (free)
2. Create test products/prices in Stripe Dashboard
3. Build subscription endpoints + webhook handler
4. Add tier-based middleware to analysis tools
5. Build billing page in frontend
6. Full E2E testing with Stripe test cards

### Phase 2: Soft Launch (When Ready)

1. Switch Stripe to live mode (requires bank account)
2. Start with Pro tier only ($19/mo)
3. Grandfather existing users as "Early Adopter Pro" (free for 3 months)
4. Collect usage data to validate pricing

### Phase 3: Production Scale

1. Add Premium tier
2. Add annual billing (20% discount)
3. Add usage-based overage billing for Premium
4. Consider Paddle/Lemon Squeezy for international tax compliance

---

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Free tier too generous | Low conversion | Start with 3 analyses/mo, adjust based on data |
| Free tier too restrictive | User churn | Monitor signup→first-analysis→paywall funnel |
| Stripe fees eat margin | Reduced profit at $19/mo | $19 - Stripe fee ($0.85) = $18.15 margin. LLM cost per analysis ~$0.02. Healthy margin. |
| India market needs Razorpay | Missed market | Add Razorpay as secondary gateway later |
| Tax compliance | Legal risk | Consider Paddle/Lemon Squeezy for Phase 3 (MoR handles tax) |

---

## Sources

- [SaaS Pricing Models: Complete 2026 Guide](https://blog.alguna.com/saas-pricing-models/)
- [Mastering Freemium Paywalls: Strategic Timing for SaaS Success](https://www.getmonetizely.com/articles/mastering-freemium-paywalls-strategic-timing-for-saas-success)
- [The 2026 Monetization Outlook: SaaS & AI Pricing Predictions](https://www.getmonetizely.com/blogs/the-2026-monetization-outlook-the-4-major-market-forces-and-13-resulting-predictions)
- [The 2026 Guide to SaaS, AI, and Agentic Pricing Models](https://www.getmonetizely.com/blogs/the-2026-guide-to-saas-ai-and-agentic-pricing-models)
- [Stripe Sandboxes Documentation](https://docs.stripe.com/sandboxes)
- [Stripe Test Cards Documentation](https://docs.stripe.com/testing)
- [Build Stripe Subscriptions Integration](https://docs.stripe.com/billing/subscriptions/build-subscriptions)
- [Implementing Stripe Subscriptions with Next.js and FastAPI](https://medium.com/@ojasskapre/implementing-stripe-subscriptions-with-supabase-next-js-and-fastapi-666e1aada1b5)
- [FastAPI Stripe Payment Gateway Integration](https://www.fast-saas.com/blog/fastapi-stripe-integration/)
- [Stripe vs Paddle vs Lemon Squeezy Comparison](https://medium.com/@muhammadwaniai/stripe-vs-paddle-vs-lemon-squeezy-i-processed-10k-through-each-heres-what-actually-matters-27ef04e4cb43)
- [Razorpay Payment Gateway Testing](https://razorpay.com/blog/payment-gateway-testing)
- [Payment Gateway API Integration Guide 2026](https://www.agilesoftlabs.com/blog/2026/03/payment-gateway-api-integration-guide)
- [Koyfin Financial Analytics](https://www.koyfin.com/)
- [Seeking Alpha Pricing](https://seekingalpha.com/)
- [SaaS Pricing Benchmark Study 2025](https://www.getmonetizely.com/articles/saas-pricing-benchmark-study-2025-insights-from-100-companies)
- [Stripe vs Lemon Squeezy for SaaS 2026](https://designrevision.com/blog/stripe-vs-lemonsqueezy)
- [Compare SaaS Payment Provider Fees](https://saasfeecalc.com/)
