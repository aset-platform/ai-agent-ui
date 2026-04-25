# Playwright auth.setup must capture Set-Cookie response headers

## When this applies

Every Playwright fixture that authenticates against a backend whose
edge proxy / middleware reads HttpOnly cookies (proxy.ts in Next 16,
or an equivalent gate). `proxy.ts` was hardened in commit b446b9e
(Sprint 8 phase A.2) to reject any request whose cookie set is missing
both `access_token` and `refresh_token` — the legacy-session hotfix
e33172d added the `||` so the proxy treats either cookie as valid.

## Bug

The original `e2e/setup/auth.setup.ts` did:

```ts
const res = await request.post(`${BACKEND}/v1/auth/login`, { data });
const { access_token } = await res.json();
fs.writeFileSync(file, JSON.stringify({
  cookies: [],
  origins: [{ origin: FRONTEND, localStorage: [
    { name: "auth_access_token", value: access_token },
  ]}],
}));
```

`request.post` doesn't include the response's `Set-Cookie` headers in
the storageState — they were just dropped. Every dependent project
opened `/dashboard` with localStorage but no cookies, the proxy
returned 302 to `/login?next=/dashboard`, the login page saw the
localStorage token and bounced back to `/dashboard`, infinite loop.
Suite collapsed to 2 passed out of ~75.

## Fix

Capture Set-Cookie via `headersArray()`, parse the cookie attributes,
**rewrite the domain from the backend host to the frontend host**
(the dev rewrite proxies `/v1/*` → backend, but proxy.ts reads the
cookie on the frontend host), and write the cookies into the
storageState JSON alongside the existing localStorage entry.

Key implementation points:
- Use `res.headersArray()` not `res.headers()` — the latter
  dedup-collapses multiple `Set-Cookie` headers into a single string.
- Parse Max-Age into an absolute `expires` timestamp (Playwright
  storageState wants epoch seconds, not a duration).
- Rewrite domain to `new URL(FRONTEND).hostname` — the backend cookie
  is set with `Domain=` unset (host-only) so it would otherwise stick
  to 127.0.0.1:8181 and never be sent to localhost:3000.

Full implementation lives at `e2e/setup/auth.setup.ts` (commit
d081827, 2026-04-25).

## Symptoms that signal this

- Playwright runs land at `/login?next=/dashboard` instead of the
  protected route.
- `redirected:` field in the Lighthouse `pw-lh-summary.json` shows
  `http://frontend-perf:3000/dashboard` for the `/login` audit (login
  page bounced an authenticated user away).
- E2E suite passes only 2 setup tests + 0 dependent tests.

## Cross-reference

- `frontend/proxy.ts` — the edge gate that requires the cookies.
- `auth/endpoints/auth_routes.py:343` — `/auth/logout` is the inverse;
  must be called on Sign Out to clear the cookies, otherwise the same
  redirect loop hits real users (commit c9e0054 fixes
  AppHeader.handleSignOut + ChatHeader.handleSignOut).
- `shared/debugging/cookie-hostname-mismatch` — related class of bug
  where cookies are set on `127.0.0.1` instead of `localhost`.
