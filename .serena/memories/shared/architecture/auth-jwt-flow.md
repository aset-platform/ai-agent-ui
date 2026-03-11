# Auth & JWT Flow

## Architecture

- JWT env propagation: `main.py` copies Pydantic settings into
  `os.environ` for `auth/dependencies.py`.
- bcrypt 5.x direct (`hashpw`/`checkpw`).
- OAuth PKCE with `code_verifier` in sessionStorage.
- Refresh token deny-list uses `TokenStore` protocol
  (`auth/token_store.py`): `InMemoryTokenStore` (default) or
  `RedisTokenStore` (set `REDIS_URL`). TTL-based auto-expiry.
- OAuth state also uses `TokenStore` (key: `oauth_state:{state}:{provider}`).
- Refresh token in HttpOnly cookie (not localStorage);
  access token in localStorage.

## Security Rules

- NEVER hardcode API keys, passwords, tokens, or connection
  strings in source.
- NEVER commit `.env` files, `credentials.json`, or private keys.
- All secrets MUST come from environment variables via
  `backend/config.py` (Pydantic Settings).
- Validate required secrets at startup — fail fast.

## Input Validation

- All user input validated at system boundaries (API endpoints,
  tool inputs).
- Pydantic models for request body validation.
- Sanitize tickers: `ticker.upper().strip()`, reject
  non-alphanumeric (except `.`).
- Dashboard: `dashboard/utils.py:check_input_safety()` checks
  length, SQL injection patterns, XSS.

## OWASP Awareness

- **Injection**: Parameterized queries only.
- **Broken auth**: JWT with proper expiry.
- **Sensitive data exposure**: Error messages MUST NOT reveal stack
  traces, file paths, or secrets to end users.
- **XSS**: No `innerHTML`, no `dangerouslySetInnerHTML` without
  sanitization.
- **SSRF**: Validate URLs before fetching.

## Critical Files

- `backend/.env` — symlink to secrets; never commit.
- `auth/password.py` — bcrypt hashing; changes can lock out users.
- `stocks/repository.py` — Iceberg schemas; breaking changes cause
  data loss.
