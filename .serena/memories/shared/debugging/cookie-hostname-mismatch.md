# Cookie Hostname Mismatch — Login Redirect Root Cause

## Problem
Users randomly redirected to login page, especially after payments or after ~60 minutes (token expiry). refreshAccessToken() always returned 401 "Missing refresh token".

## Root Cause
Frontend at `localhost:3000` called API at `127.0.0.1:8181`. These are DIFFERENT hostnames to the browser. HttpOnly cookies set by `127.0.0.1` are NOT sent on requests originating from `localhost` pages (SameSite=Lax enforcement).

## Fix
Change `NEXT_PUBLIC_BACKEND_URL` in `~/.ai-agent-ui/frontend.env.local` from `http://127.0.0.1:8181` to `http://localhost:8181`.

## Related Issues Fixed
- Cookie path: changed from `/v1/auth` to `/` for maximum compatibility
- Refresh body: removed `Content-Type: application/json` + `body: JSON.stringify({})` from frontend refresh call — FastAPI parsed {} as RefreshRequest and returned 422
- Legacy cookie cleanup: logout clears cookies at `/`, `/auth`, and `/v1/auth` paths

## Prevention
- ALWAYS use the same hostname for frontend and backend in dev (both `localhost` or both `127.0.0.1`)
- Test token refresh explicitly after login (don't just test login)
- Check backend logs for 422 (body validation) vs 401 (cookie missing) — different root causes