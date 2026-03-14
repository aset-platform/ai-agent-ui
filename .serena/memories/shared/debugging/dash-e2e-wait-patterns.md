# Dash E2E Wait Patterns

## Problem
Dash re-renders DOM via server callbacks. `page.waitForTimeout()` is unreliable — causes flaky tests under parallel workers.

## Solution: `waitForDashReady()`
Located in `e2e/utils/wait.helper.ts`. Polls DOM until no elements have `data-dash-is-loading="true"`.

```typescript
await waitForDashReady(page, timeout?);
```

Use **after any user interaction** that triggers Dash callbacks (clicks, selects, filters, pagination).

## Solution: `gotoDashPage()`
Centralizes the goto + loading + error-retry pattern for all dashboard page objects.

```typescript
await gotoDashPage(page, `/path?token=${token}`);
```

Replaces the duplicated pattern in all 6 page objects (home, analysis, admin, forecast, marketplace, insights).

## When NOT to use `waitForDashReady()`
- Frontend (React) tests — use `waitForLoadState("networkidle")` instead
- Rate-limit retries — keep `waitForTimeout()` inside catch blocks
- Sub-500ms React animation delays — keep short `waitForTimeout(500)`

## Worker Configuration
- Max 3 workers (Dash runs 1 process + 4 gthread threads)
- 4 workers would saturate all threads, causing contention
- To increase workers, bump `--threads` in `run.sh` dashboard launch
