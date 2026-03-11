# Playwright E2E Automation — Research Report

**Date**: 2026-03-07
**Project**: ai-agent-ui
**Scope**: Deep research for E2E regression testing with Playwright
**Input**: `SUPERCLAUDE_UI_AUTOMATION_PLAN.md` (10 epics)

---

## Executive Summary

Your plan covers 10 epics ranging from framework setup to AI-powered self-healing tests. This report provides a **grounded, production-ready implementation plan** tailored to your actual app surfaces (Next.js chat frontend, Plotly Dash dashboard, FastAPI backend) with specific recommendations on what to adopt, what to defer, and what to redesign from your original plan.

**Key findings:**

1. **Your app has 3 testable surfaces**: Next.js SPA (port 3000), Dash dashboard (port 8050), and FastAPI API (port 8181). Multi-app orchestration is the critical challenge.
2. **Epics 1-4 and 7 are immediately actionable** with standard Playwright patterns.
3. **Epics 2-3 (AI flow discovery + auto-generation) should be deferred** — manual test authoring with POM gives better ROI at your current scale (~43 test scenarios identified).
4. **Epics 5-6 (AI failure analysis + self-healing) are experimental** — no mature tooling exists; invest in stable selectors and good POM instead.
5. **Dash testing is the hardest part** — callback-driven rendering requires careful wait strategies.

---

## Part 1: Gap Analysis — Your Plan vs Reality

### What your plan gets right

| Item | Assessment |
|------|-----------|
| Playwright + TypeScript | Correct choice — best E2E framework for multi-browser |
| Page Object Model | Industry standard, strongly recommended |
| `data-testid` selectors | Good fallback strategy (accessibility-first is better) |
| GitHub Actions CI | Standard, well-supported by Playwright |
| Trace viewer for debugging | Built-in, production-ready |
| Screenshots on failure | Built-in, trivial to configure |

### What needs redesign

| Plan Item | Issue | Recommendation |
|-----------|-------|----------------|
| **Epic 2: AI Flow Discovery** | Over-engineered for 7 pages + 1 SPA. DOM extraction → Claude → flow graph adds complexity without proportional value at this scale. | **Replace with manual flow inventory** (already done in this report — 43 scenarios identified). Revisit when app has 50+ pages. |
| **Epic 3: Auto Test Generation** | "Generate 50-100 tests automatically" — generated tests are brittle, hard to debug, and don't understand business intent. | **Write tests manually** using POM. 43 targeted scenarios > 100 shallow auto-generated tests. |
| **Epic 5: AI Failure Analysis** | No mature tooling exists. Sending traces to Claude is possible but adds latency and cost to every failure. | **Defer**. Use Playwright trace viewer + HTML reports. Add AI analysis later as an optional post-mortem step. |
| **Epic 6: Self-Healing** | Auto-patching test code is risky — can mask real regressions. No production-grade tool exists. | **Defer**. Invest in stable selectors (`data-testid`, ARIA roles) and POM centralization instead. |
| **Project structure** | `agents/` directory with `flow_discovery.ts`, `self_heal_agent.ts` etc. adds unused scaffolding. | **Flatten**. Use standard Playwright structure: `tests/`, `pages/`, `fixtures/`, `utils/`. |
| **Language: TypeScript only** | Your Dash app is Python. Dash has `dash.testing` for Python-native testing. But Playwright TS gives unified cross-app coverage. | **Keep TypeScript** for Playwright, but add `data-testid` attributes to Dash components from Python side. |

---

## Part 2: Your App's Testable Surface (Complete Inventory)

### Frontend (Next.js — port 3000)

| Route | Page | Key Interactions |
|-------|------|-----------------|
| `/login` | Login page | Email/password form, Google OAuth button, error display |
| `/` | Chat SPA | Agent switcher, message input, send, streaming display, clear messages |
| `/` (nav) | Navigation | Grid menu → Chat / Docs / Dashboard / Insights / Admin |
| `/` (profile) | Profile menu | Edit Profile modal (name, avatar upload), Change Password modal, Sign Out |
| `/auth/oauth/callback` | OAuth callback | Handles Google redirect, error display |

### Dashboard (Plotly Dash — port 8050)

| Route | Page | Key Interactions |
|-------|------|-----------------|
| `/` | Home | Ticker search, registry dropdown, stock cards (click → analysis), pagination, per-card refresh, market filter (India/US) |
| `/analysis` | Analysis | Ticker dropdown, 7 tabs (screener/targets/dividends/risk/sectors/correlation/quarterly), sortable tables, refresh button, error overlay |
| `/forecast` | Forecast | Ticker dropdown, horizon radio, refresh button, forecast chart, accuracy metrics, error overlay |
| `/compare` | Compare | Multi-ticker select, performance chart, metrics table, correlation heatmap |
| `/marketplace` | Marketplace | Browse tickers, add/remove watchlist, pagination, search |
| `/insights` | Insights | Same tabs as analysis (RBAC-gated: superuser or `insights` permission) |
| `/admin/users` | Admin | User table, create/edit/delete modals, audit log (RBAC-gated: superuser) |

### Backend API (FastAPI — port 8181)

| Group | Endpoints | Purpose |
|-------|-----------|---------|
| Chat | `POST /chat/stream`, `GET /agents` | Streaming agent response, list agents |
| Auth | `POST /auth/login`, `/refresh`, `/logout` | JWT authentication |
| OAuth | `GET /auth/oauth/{provider}/authorize`, `POST /auth/oauth/callback` | Google PKCE flow |
| Profile | `GET/PATCH /auth/me`, `POST /auth/upload-avatar` | User profile management |
| Password | `POST /auth/password-reset/request`, `/confirm` | Password reset |
| Tickers | `GET/POST /users/me/tickers`, `DELETE .../tickers/{t}` | Watchlist management |
| Admin | `GET /users`, `POST /users`, `PATCH/DELETE /users/{id}` | User CRUD (superuser) |

### Total: 43 Test Scenarios Identified

| Area | Count | Priority |
|------|-------|----------|
| Auth flows | 6 | P0 (critical) |
| Frontend chat | 8 | P0 |
| Dashboard home | 6 | P1 |
| Dashboard analysis | 6 | P1 |
| Dashboard forecast | 4 | P1 |
| Dashboard marketplace | 4 | P2 |
| Admin | 4 | P2 |
| Error handling | 5 | P1 |
| **Total** | **43** | |

---

## Part 3: Recommended Architecture

### Project Structure (Revised)

```
e2e/
├── playwright.config.ts          # Multi-project config (frontend + dashboard)
├── package.json                  # Playwright + TypeScript deps
├── tsconfig.json
│
├── fixtures/
│   ├── auth.fixture.ts           # Login helpers, storageState per role
│   ├── app.fixture.ts            # Base URLs, health checks
│   └── test-data.fixture.ts      # Ticker data, user data factories
│
├── pages/                        # Page Object Models
│   ├── base.page.ts              # Abstract base (goto, waitForLoader)
│   ├── frontend/
│   │   ├── login.page.ts
│   │   ├── chat.page.ts
│   │   ├── profile-modal.page.ts
│   │   └── navigation.page.ts
│   └── dashboard/
│       ├── home.page.ts
│       ├── analysis.page.ts
│       ├── forecast.page.ts
│       ├── marketplace.page.ts
│       └── admin.page.ts
│
├── tests/
│   ├── auth/
│   │   ├── login.spec.ts         # Email login (valid/invalid)
│   │   ├── oauth.spec.ts         # Google OAuth flow
│   │   ├── token-refresh.spec.ts # Automatic token refresh
│   │   └── logout.spec.ts
│   ├── frontend/
│   │   ├── chat.spec.ts          # Send message, stream response, agent switch
│   │   ├── profile.spec.ts       # Edit name, upload avatar
│   │   └── navigation.spec.ts    # View switching (chat/docs/dashboard)
│   ├── dashboard/
│   │   ├── home.spec.ts          # Cards, search, pagination, refresh
│   │   ├── analysis.spec.ts      # Tabs, charts, sort, refresh
│   │   ├── forecast.spec.ts      # Generate forecast, accuracy
│   │   ├── marketplace.spec.ts   # Add/remove tickers
│   │   └── admin.spec.ts         # User CRUD (superuser)
│   └── errors/
│       ├── network-error.spec.ts # Offline, timeout, 500 responses
│       └── auth-error.spec.ts    # Expired JWT, 401 redirect
│
├── utils/
│   ├── api.helper.ts             # Direct API calls for setup/teardown
│   ├── wait.helper.ts            # Dash-specific wait utilities
│   └── selectors.ts              # Shared selector constants
│
├── setup/
│   └── auth.setup.ts             # Produces storageState files
│
└── .auth/                        # Generated storageState (gitignored)
    ├── general-user.json
    └── superuser.json
```

### `playwright.config.ts` (Production-Ready)

```typescript
import { defineConfig, devices } from "@playwright/test";

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3000";
const DASHBOARD_URL = process.env.DASHBOARD_URL || "http://127.0.0.1:8050";
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8181";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  globalTimeout: 1_800_000, // 30 min total
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  forbidOnly: !!process.env.CI,
  reporter: [
    ["html", { open: "never" }],
    ["list"],
  ],
  use: {
    headless: true,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    video: "retain-on-failure",
  },

  projects: [
    // ── Auth setup (runs first, produces storageState) ──
    {
      name: "setup",
      testMatch: /.*\.setup\.ts/,
      testDir: "./setup",
    },

    // ── Frontend tests (Next.js on port 3000) ───────────
    {
      name: "frontend-chromium",
      testDir: "./tests/frontend",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
        storageState: ".auth/general-user.json",
      },
      dependencies: ["setup"],
    },

    // ── Auth tests (no pre-auth needed) ─────────────────
    {
      name: "auth-chromium",
      testDir: "./tests/auth",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
      },
      // No dependency on setup — tests the login flow itself
    },

    // ── Dashboard tests (Dash on port 8050) ─────────────
    {
      name: "dashboard-chromium",
      testDir: "./tests/dashboard",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: DASHBOARD_URL,
        // Dashboard reads JWT from URL param, not localStorage
        // Auth handled in fixtures via ?token= query param
      },
      dependencies: ["setup"],
    },

    // ── Admin tests (superuser role) ────────────────────
    {
      name: "admin-chromium",
      testDir: "./tests/dashboard",
      testMatch: /admin\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: DASHBOARD_URL,
        storageState: ".auth/superuser.json",
      },
      dependencies: ["setup"],
    },

    // ── Error handling tests ────────────────────────────
    {
      name: "errors-chromium",
      testDir: "./tests/errors",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
      },
      dependencies: ["setup"],
    },
  ],

  // webServer starts all 3 services (or reuse if running)
  // For CI, prefer Docker Compose or run.sh
  webServer: [
    {
      command: "cd .. && ./run.sh start",
      url: BACKEND_URL + "/agents",
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
```

### Auth Setup Project (`setup/auth.setup.ts`)

```typescript
import { test as setup, expect } from "@playwright/test";

const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8181";

setup("authenticate general user", async ({ request }) => {
  const res = await request.post(`${BACKEND}/auth/login`, {
    data: {
      email: process.env.TEST_USER_EMAIL || "test@example.com",
      password: process.env.TEST_USER_PASSWORD || "TestPassword123!",
    },
  });
  expect(res.ok()).toBeTruthy();
  const { access_token } = await res.json();

  // Save storageState with the JWT in localStorage
  // (Frontend reads from localStorage; Dashboard reads from URL param)
  await setup.use({});
  // Write a minimal storageState file
  const fs = require("fs");
  fs.mkdirSync(".auth", { recursive: true });
  fs.writeFileSync(
    ".auth/general-user.json",
    JSON.stringify({
      cookies: [],
      origins: [
        {
          origin: "http://localhost:3000",
          localStorage: [
            { name: "access_token", value: access_token },
          ],
        },
      ],
    })
  );
});

setup("authenticate superuser", async ({ request }) => {
  const res = await request.post(`${BACKEND}/auth/login`, {
    data: {
      email: process.env.TEST_ADMIN_EMAIL || "admin@example.com",
      password: process.env.TEST_ADMIN_PASSWORD || "AdminPassword123!",
    },
  });
  expect(res.ok()).toBeTruthy();
  const { access_token } = await res.json();

  const fs = require("fs");
  fs.mkdirSync(".auth", { recursive: true });
  fs.writeFileSync(
    ".auth/superuser.json",
    JSON.stringify({
      cookies: [],
      origins: [
        {
          origin: "http://localhost:3000",
          localStorage: [
            { name: "access_token", value: access_token },
          ],
        },
      ],
    })
  );
});
```

---

## Part 4: Dash-Specific Testing Strategy

### The Challenge

Plotly Dash uses **server-side callbacks** that re-render DOM fragments asynchronously. There are no URL route changes when switching tabs or clicking buttons — the page stays at the same URL while DOM subtrees are replaced. This means:

1. Standard `page.waitForNavigation()` doesn't work for most interactions
2. Components may have dynamically-generated IDs (pattern-matching `{"type": "...", "index": "..."}`)
3. Data loads can take seconds (Iceberg queries, yfinance API calls)
4. Plotly charts render via JS after data arrives

### Wait Strategy for Dash

```typescript
// utils/wait.helper.ts

import { Page, Locator, expect } from "@playwright/test";

/**
 * Wait for a Dash callback to complete by watching a DOM element.
 *
 * Dash callbacks re-render target elements. We wait for the
 * element to contain expected text or become visible.
 */
export async function waitForDashCallback(
  locator: Locator,
  expectedText: string,
  timeout = 15_000,
) {
  await expect(locator).toContainText(expectedText, { timeout });
}

/**
 * Wait for a Plotly chart to render.
 *
 * Plotly injects .js-plotly-plot when the chart is drawn.
 */
export async function waitForPlotlyChart(
  page: Page,
  containerSelector: string,
  timeout = 15_000,
) {
  await page
    .locator(`${containerSelector} .js-plotly-plot`)
    .waitFor({ state: "visible", timeout });
}

/**
 * Wait for Dash loading spinner to disappear.
 */
export async function waitForDashLoading(
  page: Page,
  timeout = 20_000,
) {
  const spinner = page.locator("._dash-loading");
  // Wait for spinner to appear then disappear
  // (or skip if it never appeared)
  try {
    await spinner.waitFor({ state: "visible", timeout: 2_000 });
    await spinner.waitFor({ state: "hidden", timeout });
  } catch {
    // Spinner may never appear for fast callbacks
  }
}
```

### Selector Strategy for Dash Components

Your Dash components use explicit string IDs (e.g., `id="ticker-search-input"`, `id="stock-cards-container"`). These are stable and testable. For pattern-matching IDs (`{"type": "card-refresh-btn", "index": "AAPL"}`), Dash renders them as JSON in the `id` attribute:

```typescript
// Target a specific card's refresh button
const refreshBtn = page.locator(
  '[id*="card-refresh-btn"][id*="AAPL"]'
);
```

**Recommendation**: Add `data-testid` attributes to key Dash components where IDs are complex or auto-generated. This requires small changes in Python layout files:

```python
# dashboard/layouts/home.py — example
html.Div(
    id="stock-cards-container",
    className="g-3",
    **{"data-testid": "stock-cards-grid"},  # for Playwright
)
```

### Dashboard Page Object Example

```typescript
// pages/dashboard/home.page.ts

import { BasePage } from "../base.page";
import { waitForDashCallback, waitForDashLoading } from "../../utils/wait.helper";

export class DashboardHomePage extends BasePage {
  get tickerSearch() {
    return this.page.locator("#ticker-search-input");
  }
  get analyseBtn() {
    return this.page.locator("#search-btn");
  }
  get registryDropdown() {
    return this.page.locator("#home-registry-dropdown");
  }
  get stockCards() {
    return this.page.locator("#stock-cards-container");
  }
  get pagination() {
    return this.page.locator("#home-pagination");
  }
  get marketFilterIndia() {
    return this.page.locator("#filter-india-btn");
  }
  get marketFilterUS() {
    return this.page.locator("#filter-us-btn");
  }

  async goto(token: string) {
    await this.page.goto(`/?token=${token}`);
    await waitForDashLoading(this.page);
  }

  async searchTicker(ticker: string) {
    await this.tickerSearch.fill(ticker);
    await this.analyseBtn.click();
    // Dash navigates to /analysis via URL callback
    await this.page.waitForURL(/\/analysis/);
  }

  async getCardCount(): Promise<number> {
    await waitForDashLoading(this.page);
    return this.stockCards.locator(".stock-card").count();
  }

  cardRefreshButton(ticker: string) {
    return this.page.locator(
      `[id*="card-refresh-btn"][id*="${ticker}"]`
    );
  }
}
```

---

## Part 5: Frontend-Specific Testing Strategy

### Next.js Considerations

1. **Test against production build** (`next build && next start`) — not dev server. Your `run.sh start` already does this.
2. **Hydration timing** — use `waitUntil: 'commit'` for navigation and wait for interactive elements rather than `networkidle`.
3. **Streaming NDJSON** — chat responses arrive as a stream. Tests should wait for the `[data-testid="message-bubble"]` (or similar) to appear with expected content, not for network idle.

### Auth Handling

Your frontend stores JWT in `localStorage`. The setup project creates a `storageState` JSON with the token pre-loaded. This means authenticated tests skip the login page entirely.

For **Dashboard tests**, auth works differently — the JWT is passed as a URL query parameter (`?token=...`). The fixture should:
1. Log in via API to get a token
2. Navigate to Dash pages with `?token=${jwt}` appended

### Chat Streaming Test Pattern

```typescript
// tests/frontend/chat.spec.ts

import { test, expect } from "@playwright/test";

test("send message and receive streamed response", async ({ page }) => {
  await page.goto("/");

  // Type and send a message
  const input = page.getByPlaceholder(/type a message/i);
  await input.fill("What is Apple's current stock price?");
  await input.press("Enter");

  // Wait for streaming to start (status changes to thinking/working)
  // Then wait for final response to appear
  const lastMessage = page.locator("[data-testid='assistant-message']").last();
  await expect(lastMessage).toBeVisible({ timeout: 30_000 });
  await expect(lastMessage).not.toBeEmpty();
});
```

---

## Part 6: CI/CD Integration (GitHub Actions)

### Recommended Workflow

```yaml
# .github/workflows/e2e.yml
name: E2E Tests
on:
  pull_request:
    branches: [dev, qa, release, main]

jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      TEST_USER_EMAIL: ${{ secrets.TEST_USER_EMAIL }}
      TEST_USER_PASSWORD: ${{ secrets.TEST_USER_PASSWORD }}
      TEST_ADMIN_EMAIL: ${{ secrets.TEST_ADMIN_EMAIL }}
      TEST_ADMIN_PASSWORD: ${{ secrets.TEST_ADMIN_PASSWORD }}

    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-node@v5
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: e2e/package-lock.json
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      # Cache Playwright browsers (200-400 MB)
      - name: Cache Playwright browsers
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: playwright-${{ hashFiles('e2e/package-lock.json') }}

      - name: Install E2E deps
        run: cd e2e && npm ci && npx playwright install --with-deps chromium

      - name: Install Python deps & start services
        run: |
          python -m venv .venv
          source .venv/bin/activate
          pip install -r requirements.txt
          python auth/create_tables.py
          python stocks/create_tables.py
          ./run.sh start &
          # Wait for backend health
          timeout 60 bash -c 'until curl -s http://127.0.0.1:8181/agents; do sleep 2; done'
          # Wait for frontend
          timeout 60 bash -c 'until curl -s http://localhost:3000; do sleep 2; done'
          # Wait for dashboard
          timeout 60 bash -c 'until curl -s http://127.0.0.1:8050; do sleep 2; done'

      - name: Run E2E tests
        run: cd e2e && npx playwright test --reporter=html

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: e2e/playwright-report
          retention-days: 14

      - name: Upload traces
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-traces
          path: e2e/test-results
          retention-days: 7
```

### CI Optimization Tips

| Technique | Benefit | When to Use |
|-----------|---------|-------------|
| Cache Playwright browsers | Saves 2-3 min per run | Always |
| `chromium` only in CI | Fastest single-browser | PR checks |
| Multi-browser on `main` only | Full coverage without PR slowdown | Release gate |
| `retries: 2` in CI | Handles transient failures | Always in CI |
| `workers: 2` in CI | Reduces resource contention | Start low, tune up |
| Sharding | Splits across runners | When suite > 10 min |

---

## Part 7: Revised Epic Plan

### Phase 1 — Foundation (Week 1-2)

| Epic | Description | Status vs Original |
|------|-------------|-------------------|
| **1. Framework Setup** | Init Playwright project, config, POM base, fixtures, CI workflow | **Keep** (core of everything) |
| **1b. Add `data-testid`** | Add `data-testid` attributes to key frontend + Dash components | **New** (prerequisite for stable tests) |

### Phase 2 — Core Tests (Week 3-4)

| Epic | Description | Status vs Original |
|------|-------------|-------------------|
| **4. Auth Tests** | Login, logout, token refresh, OAuth flow, RBAC | **Keep** (P0 critical path) |
| **5. Frontend Chat Tests** | Send message, stream response, agent switch, profile edit | **Keep** (P0) |
| **6. Dashboard Home Tests** | Cards, search, pagination, refresh, error overlay | **Keep** (P1) |

### Phase 3 — Coverage Expansion (Week 5-6)

| Epic | Description | Status vs Original |
|------|-------------|-------------------|
| **7. Dashboard Analysis/Forecast** | Tab navigation, chart rendering, refresh, accuracy display | **Keep** (P1) |
| **8. Marketplace + Admin** | Add/remove tickers, user CRUD, RBAC enforcement | **Keep** (P2) |
| **9. Error Handling Tests** | Network mock, 500 responses, timeout, auth expiry | **Keep** (P1) |

### Phase 4 — CI & Reporting (Week 7)

| Epic | Description | Status vs Original |
|------|-------------|-------------------|
| **10. CI Integration** | GitHub Actions workflow, caching, artifact upload, PR gate | **Keep** (Epic 7 in original) |
| **11. Reporting** | HTML reports, trace collection, flakiness tracking | **Keep** (Epic 10 in original) |

### Deferred (Revisit at 100+ tests)

| Epic | Description | Reason for Deferral |
|------|-------------|-------------------|
| ~~Epic 2: AI Flow Discovery~~ | DOM extraction → Claude → flow graph | Over-engineered at 7 pages. Manual inventory (this report) is sufficient. |
| ~~Epic 3: Auto Test Generation~~ | Claude generates Playwright specs | Generated tests are brittle and lack business intent. Manual POM tests are more maintainable. |
| ~~Epic 5: AI Failure Analysis~~ | Send traces to Claude for root cause | Playwright trace viewer + HTML reports are sufficient. Add AI post-mortem as optional enhancement later. |
| ~~Epic 6: Self-Healing~~ | Auto-patch broken selectors | No mature tooling. Stable selectors + POM centralization prevent the problem. |

---

## Part 8: Selector Strategy Decision Matrix

| Selector Type | When to Use | Example |
|---------------|-------------|---------|
| `getByRole` | Buttons, links, form controls with accessible names | `page.getByRole("button", { name: "Analyse" })` |
| `getByLabel` | Form inputs with labels | `page.getByLabel("Email")` |
| `getByPlaceholder` | Inputs with placeholder text | `page.getByPlaceholder("Enter ticker")` |
| `getByText` | Static text content | `page.getByText("Saved Stocks")` |
| `data-testid` | Dynamic content, Dash components, complex layouts | `page.getByTestId("stock-cards-grid")` |
| `#id` selector | Dash components with explicit IDs | `page.locator("#analysis-refresh-btn")` |
| `[id*="..."]` | Dash pattern-matching IDs | `page.locator('[id*="card-refresh-btn"]')` |
| CSS class | **Avoid** — classes change with styling | ~~`.stock-card`~~ |
| XPath | **Avoid** — brittle, hard to read | ~~`//div[@class="..."]`~~ |

**Priority order**: `getByRole` > `getByLabel` > `getByTestId` > `#id` > `[id*=""]`

---

## Part 9: `data-testid` Attributes to Add

### Frontend Components (Highest Priority)

| Component | Element | Suggested `data-testid` |
|-----------|---------|------------------------|
| `ChatInput.tsx` | Message textarea | `chat-message-input` |
| `ChatInput.tsx` | Send button | `chat-send-button` |
| `ChatHeader.tsx` | Agent dropdown | `agent-selector` |
| `ChatHeader.tsx` | Clear messages button | `clear-messages-button` |
| `ChatHeader.tsx` | Profile avatar | `profile-avatar` |
| `MessageBubble.tsx` | Assistant message | `assistant-message` |
| `MessageBubble.tsx` | User message | `user-message` |
| `NavigationMenu.tsx` | Menu toggle | `nav-menu-toggle` |
| `NavigationMenu.tsx` | Each nav item | `nav-item-{name}` |
| `EditProfileModal.tsx` | Modal container | `edit-profile-modal` |
| `ChangePasswordModal.tsx` | Modal container | `change-password-modal` |
| Login page | Email input | `login-email-input` |
| Login page | Password input | `login-password-input` |
| Login page | Submit button | `login-submit-button` |
| Login page | Google OAuth button | `oauth-google-button` |
| Login page | Error message | `login-error-message` |

### Dashboard Components (Add via Python `**{"data-testid": "..."}`)

| Layout File | Element | Suggested `data-testid` |
|-------------|---------|------------------------|
| `home.py` | Stock cards container | `stock-cards-grid` |
| `home.py` | Each stock card | `stock-card-{ticker}` (via callback) |
| `home.py` | Market filter buttons | `filter-india`, `filter-us` |
| `analysis.py` | Tab container | `analysis-tabs` |
| `analysis.py` | Refresh button | Already has `id="analysis-refresh-btn"` |
| `forecast.py` | Forecast chart | `forecast-chart` |
| `forecast.py` | Accuracy row | `forecast-accuracy` |
| `marketplace.py` | Ticker grid | `marketplace-grid` |
| `admin.py` | User table | `admin-user-table` |
| `app_layout.py` | Error overlay | Already has `id="error-overlay-container"` |

---

## Part 10: Key Technical Decisions

### 1. Separate `e2e/` directory (not inside `frontend/`)

**Why**: Tests span both Next.js and Dash — they don't belong to either project. A top-level `e2e/` directory with its own `package.json` keeps concerns clean and avoids polluting frontend's `node_modules`.

### 2. Multi-project config (not separate configs)

**Why**: A single `playwright.config.ts` with multiple projects lets you run all tests together or filter by project. `npx playwright test --project=dashboard-chromium` runs only dashboard tests. Dependencies ensure auth setup runs first.

### 3. API-based auth setup (not UI login)

**Why**: UI login is tested explicitly in `auth/login.spec.ts`. All other tests use programmatic API login for speed and reliability. Dashboard tests pass the JWT as a URL param (`?token=...`) matching the real auth flow.

### 4. Chromium-only for PRs, multi-browser for releases

**Why**: Running 3 browsers triples CI time. Chromium catches 95%+ of issues. Run Firefox + WebKit only on `main` merges or release branches.

### 5. Docker Compose for CI (future)

**Why**: Your app has 3 services (backend, frontend, dashboard). Docker Compose gives reproducible, isolated CI environments. Start with `run.sh` for simplicity, migrate to Compose when the suite stabilizes.

---

## Part 11: Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Dash callback timing | Tests flaky due to slow callbacks | Use `waitForDashCallback()` helper, not fixed timeouts |
| LLM responses non-deterministic | Chat tests can't assert exact content | Assert structure (response exists, not empty) not content |
| Long refresh operations (yfinance) | Tests timeout during data refresh | Mock yfinance responses via `page.route()` for API calls, or use shorter timeouts with retry |
| Dynamic Plotly chart DOM | Selectors break when Plotly updates | Target wrapper containers, not Plotly internals |
| Multiple services startup order | Backend must be ready before frontend | Health-check waits in CI workflow |
| storageState expiry | JWT expires mid-test-suite | Use fresh tokens per test worker via fixture |

---

## Part 12: Estimated Test Counts (Target)

| Phase | Tests | Cumulative |
|-------|-------|-----------|
| Phase 1 (foundation) | 0 (setup only) | 0 |
| Phase 2 (core) | 20 | 20 |
| Phase 3 (expansion) | 15 | 35 |
| Phase 4 (CI + errors) | 8 | 43 |
| **Total (achievable)** | | **43** |

This is more valuable than 100 auto-generated shallow tests. Each test covers a real user journey with meaningful assertions.

---

## Evidence Gaps

| Topic | Status |
|-------|--------|
| Playwright MCP server features | No authoritative docs found — treat as experimental |
| AI self-healing tools maturity | No production-grade tool exists — defer |
| Plotly chart interaction recipes | Limited — use container-level assertions, not pixel-level |
| Visual regression (Percy/Applitools) | Not researched in depth — Chromatic has Playwright support |

---

## References

- [Playwright Documentation](https://playwright.dev/docs/intro) — config, POM, CI, traces
- [Dash Testing Guide](https://dash.plotly.com/testing) — `dash.testing` patterns (Python-native)
- [Next.js Playwright Guide](https://nextjs.org/docs/pages/guides/testing/playwright)
- [Scaling Playwright Automation](https://dzone.com/articles/scaling-playwright-test-automation)
- [POM with TypeScript](https://medium.com/@anandpak108/page-object-model-in-playwright-with-typescript-best-practices-133fb349c462)
- [Playwright Locators Guide](https://momentic.ai/blog/playwright-locators-guide)
- [Chromatic Visual Testing](https://www.chromatic.com/blog/how-to-visual-test-ui-using-playwright/)

---

## Next Steps

After reviewing this report, recommended actions:

1. **`/sc:implement`** — Phase 1: Initialize `e2e/` project, `playwright.config.ts`, POM base classes, auth fixtures
2. **`/sc:implement`** — Add `data-testid` attributes to frontend and dashboard components
3. **`/sc:implement`** — Phase 2: Write core auth + chat + dashboard home tests
4. **`/sc:test`** — Validate tests run against live services
5. **`/sc:build`** — Set up GitHub Actions workflow
