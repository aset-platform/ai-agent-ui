# Payment Redirect → Login Page Bug (Fixed Mar 28, 2026)

## Symptom
After successful Razorpay payment, user redirected to login page instead of staying on the billing tab.

## Root Causes (3 layered issues)

### 1. NEXT_PUBLIC_BACKEND_URL = 127.0.0.1 (not localhost)
- Frontend at localhost:3000, API at 127.0.0.1:8181 = different hostnames
- Browser won't send HttpOnly cookies set by 127.0.0.1 to localhost pages
- Fix: set to http://localhost:8181 in ~/.ai-agent-ui/frontend.env.local
- CRITICAL: NEXT_PUBLIC_ vars baked at build time — requires full frontend restart

### 2. SameSite=strict on refresh token cookie
- Strict blocks cookies on ALL cross-site navigations
- Razorpay redirects back from razorpay.com → app = cross-site
- Fix: SameSite=lax (allows GET redirects, blocks cross-site POST)
- File: auth/endpoints/auth_routes.py line 65

### 3. Blocking refreshAccessToken() in Razorpay handler
- BillingTab.tsx handler: `await refreshAccessToken()` after payment
- If refresh fails → clearTokens() → window.location.href = "/login"
- User loses session after a SUCCESSFUL payment
- Fix: fetchSubscription() first, refreshAccessToken().catch(() => {}) non-blocking
- Webhook updates tier server-side; next natural refresh picks up new JWT claims

## Prevention
- Always use same hostname for frontend and backend (both localhost)
- Never await refreshAccessToken() in payment callbacks
- Test payment flow end-to-end after any auth/cookie changes
