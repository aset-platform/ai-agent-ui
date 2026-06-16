/**
 * E2E coverage for the "Build RSI(2) replay fixture" item in the
 * watchlist ⋮ overflow menu (Paper Replay Fixture Builder epic).
 *
 * Menu item testid : ``watchlist-build-fixture``
 * Result testid    : ``watchlist-build-fixture-result``
 *
 * Project = ``frontend-chromium`` (superuser storage state —
 * file name ``watchlist-build-fixture.spec.ts`` chosen to avoid
 * the project-level testIgnore patterns for analytics, dashboard,
 * insights, marketplace, portfolio, admin, and theme-consistency).
 *
 * The ``.auth/superuser.json`` storageState provides auth — no
 * explicit /auth/login call per CLAUDE.md §5.14.
 *
 * Cases:
 * 1. Build-fixture item is visible in the overflow menu.
 * 2. Clicking the item shows a result message that matches
 *    ``/Built|No oversold/`` (backend build endpoint called;
 *    both "Built <filename>" on success and "No oversold
 *    (RSI2≤5)…" when no trigger dates are found are valid
 *    real-data outcomes — not bugs).
 *
 * Per CLAUDE.md §5.14: element waits (not networkidle), 1 worker
 * locally, superuser storage state, POM-based.
 */

import { test, expect } from "@playwright/test";

import { DashboardHomePage } from "../../pages/frontend/dashboard-home.page";

test.use({ storageState: ".auth/superuser.json" });

// The build endpoint scans ~68 tickers × lookback_days over Iceberg;
// mark slow so Playwright triples the default 30 s test timeout → 90 s.
test.slow();

test.describe("Watchlist — Build RSI(2) replay fixture", () => {
  test(
    "build-fixture item is visible in the overflow menu",
    async ({ page }) => {
      const dash = new DashboardHomePage(page);
      await dash.gotoDashboard();

      // Switch to Watchlist tab (Portfolio is the default tab).
      await page
        .getByRole("button", { name: /watchlist/i })
        .click();

      await dash.openWatchlistOverflow();

      const item = dash.buildFixtureItem();
      await item.waitFor({ state: "attached", timeout: 5_000 });
      await expect(item).toBeVisible();
    },
  );

  test(
    "clicking the build item shows a result message",
    async ({ page }) => {
      const dash = new DashboardHomePage(page);
      await dash.gotoDashboard();

      await page
        .getByRole("button", { name: /watchlist/i })
        .click();

      await dash.openWatchlistOverflow();

      const item = dash.buildFixtureItem();
      await item.waitFor({ state: "attached", timeout: 5_000 });
      await item.click();

      // The menu closes on click; the result <p> is rendered
      // outside the menu, so it remains attached.
      // Both "Built <filename> · N tickers · N trigger dates"
      // and "No oversold (RSI2≤5) setups in the lookback window"
      // are valid real-data outcomes — accept either.
      // Generous timeout: the build scans ~68 tickers over
      // Iceberg and can take ~30 s in the default 60-day window.
      await expect(dash.buildFixtureResult()).toContainText(
        /Built|No oversold/,
        { timeout: 60_000 },
      );
    },
  );
});
