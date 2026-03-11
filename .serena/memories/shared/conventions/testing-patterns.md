# Testing Patterns

## Running Tests

```bash
python -m pytest tests/ -v             # all (273 tests)
python -m pytest tests/backend/ -v     # backend
python -m pytest tests/dashboard/ -v   # dashboard
cd frontend && npx vitest run          # frontend (18 tests)

# E2E (Playwright — 49 tests, requires live services)
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
