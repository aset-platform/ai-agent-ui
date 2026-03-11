# E2E Testing (Playwright)

The `e2e/` directory contains a Playwright test suite covering all 3 app surfaces: Next.js frontend (port 3000), Plotly Dash dashboard (port 8050), and FastAPI backend (port 8181).

---

## Quick Start

```bash
cd e2e
npm install                          # first time only
npx playwright install chromium      # first time only

npm test                             # run all 49 tests (headless)
npx playwright test --headed         # visible browser
npx playwright test --ui             # interactive UI mode (best for exploration)
```

All 3 services must be running (`./run.sh start`). The Playwright config auto-starts them via `webServer` if not already running.

---

## Project Structure

```
e2e/
├── playwright.config.ts       # 6 projects: setup, auth, frontend, dashboard, admin, errors
├── setup/auth.setup.ts        # API login -> storageState for general + superuser
├── fixtures/auth.fixture.ts   # userToken + adminToken fixtures for Dash ?token= param
├── pages/                     # Page Object Models (10 classes)
│   ├── base.page.ts           # Abstract base (goto, tid, loc helpers)
│   ├── frontend/
│   │   ├── login.page.ts
│   │   ├── chat.page.ts
│   │   └── ...
│   └── dashboard/
│       ├── home.page.ts
│       ├── analysis.page.ts
│       └── ...
├── tests/
│   ├── auth/                  # 8 tests (login, logout, OAuth, token refresh)
│   ├── frontend/              # 15 tests (chat, navigation, profile, token)
│   ├── dashboard/             # 19 tests (home, analysis, forecast, marketplace, admin)
│   └── errors/                # 5 tests (network errors, auth expiry)
└── utils/
    ├── selectors.ts           # FE + DASH data-testid constants
    ├── wait.helper.ts         # Dash-specific wait utilities
    └── api.helper.ts          # apiLogin, apiLinkTicker, apiGetProfile
```

---

## Running Tests

### By project

```bash
npx playwright test --project=frontend-chromium   # frontend only
npx playwright test --project=dashboard-chromium  # dashboard only
npx playwright test --project=auth-chromium       # auth only
npx playwright test --project=admin-chromium      # admin only
npx playwright test --project=errors-chromium     # error handling only
```

### By file

```bash
npx playwright test tests/frontend/chat.spec.ts
npx playwright test tests/dashboard/home.spec.ts
```

### Debug mode (step through)

```bash
npx playwright test tests/frontend/chat.spec.ts --debug
```

---

## Test Coverage (49 tests)

| Area | Tests | What's Covered |
|------|-------|----------------|
| Auth | 8 | Login form, valid/invalid credentials, redirect, OAuth button, logout |
| Chat | 8 | Page load, send message, stream response, agent switch, clear, Enter key, status badge, multi-turn |
| Navigation | 3 | Menu toggle, dashboard iframe, docs iframe |
| Profile | 2 | Edit profile modal, change password modal |
| Token refresh | 2 | 401 redirect, pre-request refresh |
| Dashboard home | 6 | Stock cards, search, dropdown, pagination, market filter, per-card refresh |
| Analysis | 4 | Page load, ticker select, tab navigation, refresh |
| Forecast | 4 | Page load, ticker select, no-ticker warning, accuracy metrics |
| Marketplace | 3 | Grid load, add/remove buttons, pagination |
| Admin | 3 | User table, row count, RBAC forbidden |
| Error handling | 5 | Backend 500, auth expiry (frontend + dashboard), dashboard refresh failure, offline |

---

## Auth Strategy

### Frontend tests

The `setup` project logs in via API and produces `storageState` files (`.auth/general-user.json`, `.auth/superuser.json`) with JWTs pre-loaded in `localStorage`. Frontend tests use these directly — no login UI needed.

### Dashboard tests

Dash reads JWT from URL query params, not `localStorage`. The `auth.fixture.ts` provides `userToken` and `adminToken` fixtures. Page objects navigate with `?token=${jwt}` appended.

---

## Dash-Specific Patterns

### Wait helpers (`utils/wait.helper.ts`)

| Helper | Purpose |
|--------|---------|
| `waitForDashLoading(page)` | Waits for `._dash-loading` spinner to appear then disappear |
| `waitForDashCallback(locator, text)` | Waits for a callback to render expected text |
| `waitForPlotlyChart(page, selector)` | Waits for `.js-plotly-plot` inside a container |

### Selectors

- Dash components with explicit `id=` attributes: use `page.locator("#id")`
- Pattern-matching IDs (e.g. `{"type": "card-refresh-btn", "index": "AAPL"}`): use `page.locator('[id*="card-refresh-btn"]')`
- `data-testid` attributes added to key components: use `page.getByTestId("name")`

### Known gotchas

| Issue | Workaround |
|-------|-----------|
| `dbc.*` components reject `data-testid` kwargs | Wrap in `html.Div(**{"data-testid": "..."})` |
| Dash debug toolbar overlays bottom of page | Use `{ force: true }` on clicks near bottom |
| Dash file watcher restarts on any file change | Test `outputDir` is `/tmp/e2e-test-results` (outside project tree) |
| Dash callbacks are async — no URL changes | Use `waitForDashLoading()` instead of `waitForNavigation()` |

---

## Frontend-Specific Patterns

### React 19 controlled inputs

Playwright's `fill()` does NOT reliably trigger React 19's synthetic `onChange` on controlled `<textarea>` / `<input>`. Always use:

```typescript
await element.click();
await element.pressSequentially(text, { delay: 30 });
```

### NDJSON stream mocking

The chat endpoint returns NDJSON. Mock with:

```typescript
await page.route("**/chat/stream", (route) => {
  const body = JSON.stringify({
    type: "final",
    response: "Mocked reply",
  }) + "\n";
  route.fulfill({
    status: 200,
    contentType: "application/x-ndjson",
    body,
  });
});
```

---

## CI Integration

`.github/workflows/e2e.yml` runs on every PR:

- Chromium only (faster than 3 browsers)
- Caches Playwright browsers (~200-400 MB)
- `retries: 2` for transient failures
- Uploads HTML report (14 days) and traces on failure (7 days)

---

## Viewing Results

```bash
# HTML report (after any test run)
npx playwright show-report

# Trace viewer (for failed retries)
npx playwright show-trace /tmp/e2e-test-results/<test-folder>/trace.zip
```
