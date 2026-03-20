# Testing Patterns

## Running Tests

```bash
python -m pytest tests/ -v             # all (~320 tests)
python -m pytest tests/backend/ -v     # backend (~275 tests)
python -m pytest tests/dashboard/ -v   # dashboard (~45 tests)
cd frontend && npx vitest run          # frontend (22 tests)

# E2E (Playwright — ~91 tests, requires live services)
cd e2e && npm test                     # all projects
npx playwright test --project=frontend-chromium
npx playwright test --project=dashboard-chromium
npx playwright test --headed           # visible browser
npx playwright test --ui              # interactive UI mode
```

## Test-After-Feature Rule

After every feature addition and successful smoke test, update the
test suite immediately — happy path + 1 error path minimum. Do NOT
defer test writing to a later session.

## E2E (Playwright) Gotchas

- Use `pressSequentially()` not `fill()` for React 19 controlled
  inputs (textarea/input with `value={state}` + `onChange`).
- `dbc.*` components (dash_bootstrap_components 2.0.4) do NOT accept
  `data-testid` — wrap in `html.Div(**{"data-testid": "..."})`.
- Keep Playwright `outputDir` outside the project tree (`/tmp/`) to
  avoid triggering the Dash debug reloader.
- Use `{ force: true }` for clicks blocked by Dash debug toolbar.
- Single-threaded Dash server can't handle parallel browser
  connections — use `--workers=1` for reliable E2E pass.

## E2E Auth Token Caching

Auth tokens are cached in storageState files
(`.auth/general-user.json`, `superuser.json`) to eliminate redundant
`/auth/login` calls per E2E run. Prevents 429 rate-limit errors.

## Dashboard Test Import Issues

Importing dashboard callback modules triggers the full callback chain
via `__init__.py`. This can require packages like `holidays`.
Replicate constants in test files instead of importing from callback
modules.

## See Also

- `shared/debugging/test-isolation-gotchas` — Test ordering, WS testing,
  patch bleed issues
- `shared/debugging/mock-patching-gotchas` — Patching at source module
