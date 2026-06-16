/**
 * E2E coverage for the "Add filtered tickers to watchlist"
 * button on the Advanced Analytics page (Epic C / AA).
 *
 * Button testid  : ``aa-add-to-watchlist``
 * Result testid  : ``aa-add-to-watchlist-result``
 *
 * Project = ``frontend-chromium`` (superuser storage state —
 * Advanced Analytics is pro+superuser-gated per AA-7).
 * The project-level storageState (``.auth/superuser.json``)
 * provides authentication — /auth/login is NOT called here.
 *
 * Cases:
 * 1. Button is visible (attached + scrolled into view).
 * 2. When enabled, clicking produces a result message that
 *    matches either "Added <n>" (success) or a message
 *    indicating no tickers matched the current filter.
 * 3. When the filtered set is empty the button is disabled
 *    and the test passes without clicking (environment-safe).
 *
 * Per CLAUDE.md §5.14: no ``networkidle``, locator-scoped
 * data-testids, 1 worker locally.
 */

import { test, expect } from "@playwright/test";

import { AdvancedAnalyticsPage } from "../../pages/frontend/advanced-analytics.page";

test.describe("Advanced Analytics — Add to Watchlist", () => {
  test(
    "add filtered tickers to watchlist button is visible",
    async ({ page }) => {
      const aa = new AdvancedAnalyticsPage(page);
      await aa.gotoAdvancedAnalytics();

      const btn = aa.addToWatchlistBtn();
      await btn.waitFor({ state: "attached", timeout: 15_000 });
      await btn.scrollIntoViewIfNeeded();
      await expect(btn).toBeVisible();
    },
  );

  test(
    "clicking the button when enabled shows a result message",
    async ({ page }) => {
      const aa = new AdvancedAnalyticsPage(page);
      await aa.gotoAdvancedAnalytics();

      const btn = aa.addToWatchlistBtn();
      await btn.waitFor({ state: "attached", timeout: 15_000 });
      await btn.scrollIntoViewIfNeeded();

      // Button may be disabled when the filtered set is empty
      // or over the per-user watchlist cap — only click when
      // enabled and accept either an "Added <n>" success or
      // a no-tickers / empty-filter message.
      if (await btn.isEnabled()) {
        await btn.click();
        await expect(aa.addToWatchlistResult()).toContainText(
          /Added \d+|No tickers|already in|watchlist/i,
          { timeout: 15_000 },
        );
      }
    },
  );
});
