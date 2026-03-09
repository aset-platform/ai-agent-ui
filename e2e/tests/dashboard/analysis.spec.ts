/**
 * E2E tests for the Dash analysis page.
 */

import { test, expect } from "../../fixtures/auth.fixture";

import { DashAnalysisPage } from "../../pages/dashboard/analysis.page";

test.describe("Dashboard analysis", () => {
  let analysisPage: DashAnalysisPage;

  test.beforeEach(async ({ page, userToken }) => {
    analysisPage = new DashAnalysisPage(page);
    await analysisPage.gotoWithToken(userToken);
  });

  test("analysis page loads with ticker dropdown", async () => {
    await expect(analysisPage.tickerDropdown).toBeVisible({
      timeout: 15_000,
    });
  });

  test("select ticker → chart renders", async ({ page }) => {
    await analysisPage.selectTicker("RELIANCE.NS");
    // Wait for a Plotly chart to appear
    const chart = page.locator(".js-plotly-plot").first();
    await expect(chart).toBeVisible({ timeout: 15_000 });
  });

  test("tab navigation loads different content", async ({
    page,
  }) => {
    // Click through the top-level analysis tabs
    const tabNames = ["Forecast", "Compare Stocks"];
    for (const tab of tabNames) {
      await analysisPage.clickTab(tab);
      // Each tab should show some content (not empty)
      const content = page.locator(
        ".tab-pane.active, .tab-content",
      );
      await expect(content.first()).toBeVisible({
        timeout: 15_000,
      });
    }
  });

  test("refresh button shows success/error status", async () => {
    await analysisPage.selectTicker("RELIANCE.NS");
    await analysisPage.refreshBtn.click();
    // Status should update within 60s (refresh is slow)
    await expect(analysisPage.refreshStatus).not.toBeEmpty({
      timeout: 60_000,
    });
  });
});
