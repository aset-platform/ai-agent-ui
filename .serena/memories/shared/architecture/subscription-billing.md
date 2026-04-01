# Subscription & Billing Architecture

## Data Model
- Subscription fields on auth.users: subscription_tier, subscription_status, razorpay_customer_id, razorpay_subscription_id, stripe_customer_id, stripe_subscription_id, monthly_usage_count, usage_month, subscription_start_at, subscription_end_at
- auth.usage_history table: user_id, month (YYYY-MM), usage_count, tier, archived_at
- Tiers: free (3 analyses/mo), pro (30/mo, Rs499), premium (unlimited, Rs1499)
- Config: backend/subscription_config.py (TIER_ORDER, USAGE_QUOTAS, pricing)

## Razorpay Integration
- Endpoints in auth/endpoints/subscription_routes.py
- POST /v1/subscription/checkout — PATCH existing sub for upgrades, POST for new
- GET /v1/subscription — reads from Iceberg (NOT JWT)
- POST /v1/subscription/cancel — resets tier to free, clears sub_id
- POST /v1/webhooks/razorpay — handles charged, cancelled, payment.failed
- POST /v1/subscription/cleanup?dry_run=true — triage orphaned subs
- _safe_update() retries on Iceberg CommitFailedException
- _find_user_by_razorpay() prioritises sub_id over cust_id

## Usage Tracking
- increment_usage() in backend/usage_tracker.py — called from all 4 chat routes
- Lazy auto-reset: if usage_month != current month, archive + reset before increment
- No cron needed — reset happens on first API call of new month
- Admin endpoints: GET /admin/usage-stats, POST /admin/reset-usage, POST /admin/reset-usage/selected, GET /admin/usage-history

## Frontend
- BillingTab in EditProfileModal (3rd tab)
- UsageBadge in ChatHeader (compact pill)
- UpgradeBanner below AppHeader (SWR-based, dismissible)
- Billing in profile dropdown menu
- Admin Maintenance tab: subscription cleanup, usage reset, data retention, gap analysis

## Guards
- require_tier(min_tier) — 403 if tier too low
- check_usage_quota() — 429 when quota exhausted
- Both in auth/dependencies.py

## Critical Rule
ALWAYS read subscription tier/status from Iceberg via repo.get_by_id(), NEVER from JWT UserContext. JWT is a stale cache.