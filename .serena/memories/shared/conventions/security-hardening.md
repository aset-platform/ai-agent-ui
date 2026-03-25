# Security Hardening Conventions

## Authentication & Authorization
- Chat endpoints (`/v1/chat`, `/v1/chat/stream`) MUST use `Depends(get_current_user)` — derive `user_id` from JWT, never from request body
- WebSocket `user_id` from `user_ctx["user_id"]` only — never trust client-supplied `msg.get("user_id")`
- All admin endpoints use `Depends(superuser_only)`

## Cookie Security
- `secure` flag: env-gated via `not get_settings().debug` (True in production)
- `samesite="strict"` always (not "lax")
- `httponly=True` always
- `localhost` and `127.0.0.1` are DIFFERENT hostnames for cookies — use consistent hostname

## Webhook Security
- Signature verification is MANDATORY — reject with 503 if secret not configured
- Never fall through to process unsigned webhooks
- Validate `tier` values from webhook metadata against allowlist before DB write
- `_plan_id_to_tier()` returns `None` for unknown plans (not a default)

## Rate Limiting
- ALL auth endpoints must have `@limiter.limit()` — including `/auth/login/form` and `/auth/password-reset/confirm`
- `get_remote_address` is spoofable via `X-Forwarded-For` — configure trusted proxy in production

## Input Validation
- Use Pydantic `Literal` types for enum fields (tier, gateway)
- Use `Field(gt=0, le=...)` for numeric constraints
- Cap unbounded lists: `history: list = Field(default=[], max_length=100)`
- `avatar_url` restricted to `^https?://` pattern

## Error Handling
- Quota enforcement MUST fail closed (503), not open (pass)
- Password reset token only in response when `settings.debug` is True
- Refresh token logs at DEBUG level, never INFO
- No plaintext passwords in log messages

## Security Headers
Required on all responses:
- `Content-Security-Policy`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Strict-Transport-Security` (production only)
