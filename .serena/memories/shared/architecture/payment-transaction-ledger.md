# Payment Transaction Ledger

## Table: auth.payment_transactions
Append-only Iceberg table for reconciliation and dispute resolution.

Columns: transaction_id, user_id, gateway, event_type, gateway_event_id, subscription_id, customer_id, amount, currency, tier_before, tier_after, status, raw_payload, created_at

## What Gets Logged
- Razorpay webhook: charged, cancelled, payment_failed
- Stripe webhook: checkout_completed, subscription_deleted, payment_failed
- PATCH upgrades: Razorpay (subscription.edit) and Stripe (Subscription.modify)
- User-initiated cancel: logged as "user_cancelled"

## Amount Sources
- Razorpay charged: TIER_PRICE_INR from subscription_config.py
- Stripe checkout: session.amount_total / 100 (cents to dollars)
- Upgrades: TIER_PRICE_INR or TIER_PRICE_USD for target tier

## Admin Endpoint
GET /v1/admin/payment-transactions?user_id=&gateway=&limit=50
Enriches response with user_name and user_email from Iceberg join.
Replaces NaN floats with None for JSON compatibility.

## Admin UI
Admin → Transactions tab: Date, User (ID), Name (full_name + email), Gateway (colored badge), Event, Source (User/Webhook badge), Amount, Tier Change, Status, Details (expandable raw JSON)