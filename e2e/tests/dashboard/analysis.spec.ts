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
    await analysisPage.selectTicker("AAPL");
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
    test.slow(); // 3x timeout — refresh runs in background
    await analysisPage.selectTicker("AAPL");
    await analysisPage.refreshBtn.click();
    // Poll callback writes ✓ or ✗ when the background
    // refresh future completes.
    await expect(
      analysisPage.refreshStatus,
    ).toContainText(/[✓✗]/, { timeout: 120_000 });
  });
});
