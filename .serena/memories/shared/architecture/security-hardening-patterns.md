# Security Hardening Patterns

## Webhook Signature Verification
Always fail closed — reject if webhook secret is not configured:
```python
if not secret:
    raise HTTPException(status_code=503, detail="Webhook not configured")
if not verify_signature(body, signature, secret):
    raise HTTPException(status_code=400, detail="Invalid signature")
```
Never skip verification in production. The `503` signals misconfiguration, not a client error.

## Auth on Chat Endpoints
Chat endpoints (`/v1/chat`, `/v1/chat/stream`) must derive `user_id` from JWT, never from the request body. The `ChatRequest.user_id` field is untrusted input.
```python
async def _chat(req: ChatRequest, user: UserContext = Depends(get_current_user)):
    user_id = user.user_id  # ignore req.user_id
```

## Cookie Security
- `secure=True` in production (gated behind `not settings.debug`)
- `samesite="strict"` always for auth cookies
- `httponly=True` for refresh tokens

## Quota Enforcement — Fail Closed
Never `except: pass` on quota checks — reject with 503:
```python
except Exception:
    logger.error("Quota check failed — rejecting", exc_info=True)
    raise HTTPException(status_code=503, detail="Usage tracking unavailable")
```

## Input Validation
- `CheckoutRequest`: `Literal["pro", "premium"]` for tier, `Literal["razorpay", "stripe"]` for gateway
- `AddPortfolioRequest`: `Field(gt=0, le=1_000_000)` for quantity/price, `max_length=500` for notes
- `ChatRequest.history`: `max_length=100` to prevent unbounded payloads
- Razorpay `_plan_id_to_tier()`: return `None` for unknown plan IDs (don't default to "pro")
- Stripe tier metadata: validate against allowed set before DB write

## Security Headers
`_SecurityHeadersMiddleware` should include:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'`
- `Strict-Transport-Security` (production only)

## Rate Limiting
All auth endpoints need rate limits — including `/auth/login/form` and `/auth/password-reset/confirm` (not just `/auth/login`). Use `request: Request` param for slowapi.

## Password Reset Token
Gate `reset_token` in response behind `settings.debug`. Production response returns only a generic message.

## Logging Hygiene
- Cookie names at DEBUG level only (not INFO)
- Never log plaintext passwords (seed scripts)
- Script placeholder JWT keys must be 32+ chars