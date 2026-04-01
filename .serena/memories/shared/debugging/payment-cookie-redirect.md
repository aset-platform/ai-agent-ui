# Payment Redirect Cookie Fix

## Problem
After Razorpay/Stripe payment completion, the redirect back to the app
failed to maintain the auth session. The user was redirected to /login
instead of the success page.

## Root Cause
`SameSite=strict` on the refresh token cookie blocked the cookie from
being sent on the redirect from the payment gateway (cross-site navigation).
Payment gateways redirect via 302 from their domain to localhost — this is
a cross-site navigation that `SameSite=strict` blocks.

## Fix
Changed `SameSite=strict` to `SameSite=lax` in the auth cookie settings.
`lax` allows cookies on top-level navigations (GET redirects) but still
blocks cross-site POST requests (CSRF protection maintained).

Additionally, `refreshAccessToken` in the Razorpay success handler was
made non-blocking (fire-and-forget with `.catch()`) to avoid blocking
the payment confirmation UI while the token refreshes.

## Files
- Auth cookie configuration (SameSite setting)
- Frontend Razorpay handler (non-blocking refresh)
