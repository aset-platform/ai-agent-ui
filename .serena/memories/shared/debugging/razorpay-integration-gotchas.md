# Razorpay Integration Gotchas

## Webhook URL Must Match Exactly
- Razorpay sends to the URL configured in Dashboard
- Our route: /v1/subscription/webhooks/razorpay
- Also aliased: /v1/webhooks/razorpay (for shorter Dashboard config)
- Use ngrok for localhost: ngrok http 8181

## Signature Verification
- RAZORPAY_WEBHOOK_SECRET empty = skip verification (test mode convenience)
- Production: MUST set secret and verify HMAC-SHA256
- Header: X-Razorpay-Signature

## Test Cards for Subscriptions
- 4111 1111 1111 1111 does NOT support recurring payments
- Use: 5267 3181 8797 5449 (Mastercard, supports recurring)
- UPI test: success@razorpay

## Upgrade Path
- PATCH /subscriptions/:id with plan_id + schedule_change_at="now"
- Razorpay handles pro-rata billing automatically
- Do NOT cancel+create — creates orphaned subscriptions
- Frontend: if response.upgraded=true, skip Razorpay modal (server-side PATCH already done)

## Concurrent Writes
- Razorpay webhook + cancel endpoint can race on same user row
- Iceberg throws CommitFailedException
- Fix: _safe_update() with 3 retries

## Orphaned Subscriptions
- Created when checkout() makes new sub without cancelling old
- Triage: matched (current sub_id), orphaned (same cust, wrong sub), unlinked (no user)
- Only auto-cancel orphaned; unlinked = manual review