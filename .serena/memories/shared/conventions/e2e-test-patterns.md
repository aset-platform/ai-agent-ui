# E2E Playwright Test Patterns

## Project Structure
```
e2e/
  setup/auth.setup.ts          — creates auth contexts (general + superuser)
  fixtures/                    — test fixtures (auth, portfolio, subscription)
  pages/frontend/              — Page Object Models (BasePage → specific pages)
  tests/frontend/              — test specs
  tests/performance/           — Lighthouse/Core Web Vitals
  utils/selectors.ts           — FE testid constants
  utils/api.helper.ts          — backend API helpers
  utils/wait.helper.ts         — waitForPageReady, waitForPlotlyChart
```

## Playwright Config Projects
- `setup` — auth context creation (runs first)
- `frontend-chromium` — general user tests (storageState: general-user.json)
- `analytics-chromium` — dashboard/analytics/insights/marketplace tests
- `admin-chromium` — admin tests (storageState: superuser.json)
- `errors-chromium` — error handling tests
- `performance` — Lighthouse/Core Web Vitals

## Patterns

### Page Object Model
All pages extend `BasePage`. Use `this.tid(FE.selectorName)` for testid lookups.

### Auth Fixtures
```typescript
import { test, expect } from "../../fixtures/portfolio.fixture";
// provides: page, seededPortfolio, userToken
```

### Mocking APIs
```typescript
await page.route("**/v1/subscription/checkout", (route) =>
  route.fulfill({ status: 200, body: JSON.stringify({...}) })
);
```

### Mocking Payment SDKs
```typescript
await page.addInitScript(() => {
  (window as any).Razorpay = class { open() { /* mock */ } };
});
```

### WebSocket Testing
```typescript
page.on("websocket", (ws) => {
  ws.on("framereceived", (frame) => { /* inspect */ });
});
```

### Visual Regression
```typescript
await expect(page.locator("[data-testid=chart]"))
  .toHaveScreenshot("chart-light.png");
```

### data-testid Convention
- All interactive elements need `data-testid`
- Constants in `e2e/utils/selectors.ts` FE object
- Pattern: `component-element` (e.g., `add-stock-submit`, `billing-gateway-stripe`)

## Test Categories
- UI rendering + navigation: well covered (173 tests)
- Business workflows: portfolio CRUD, payments, subscription lifecycle, admin CRUD
- Performance: Lighthouse thresholds (LCP < 2.5s, FCP < 1.8s, TBT < 300ms, CLS < 0.1)
