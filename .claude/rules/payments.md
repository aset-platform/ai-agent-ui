---
paths:
  - "auth/endpoints/subscription_routes.py"
  - "auth/repo/user_writes.py"
  - "auth/service.py"
  - "backend/subscription_config.py"
  - "backend/db/models/payment.py"
  - "**/*razorpay*"
---

# Payments rules — Razorpay INR + Stripe USD (auto-loads when touching payment code)

> Lazy-loaded via `paths:` frontmatter. Mirrors what was CLAUDE.md §5.11.
> Deep detail → Serena memories `subscription-billing`, `payment-transaction-ledger`,
> `razorpay-integration-gotchas`, `security-hardening-patterns`.

- **Read tier from Iceberg via `repo.get_by_id()`, NOT JWT** (stale cache). Same for `subscription_status`.
- **Webhook signature verification MANDATORY** — fail-closed 503 if secret missing. `_plan_id_to_tier()` returns `None` for unknown plans.
- **Upgrades use PATCH not cancel+create** — Razorpay `subscription.edit` w/ `schedule_change_at="now"`; Stripe `Subscription.modify`. Cancel+create makes orphan subs.
- Concurrent writes: `_safe_update()` retries 3× on `CommitFailedException`.
- Every payment writes to `auth.payment_transactions` ledger (`event_type` + `tier_before/after` + raw payload).
