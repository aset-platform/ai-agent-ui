/**
 * E2E tests for the Dash insights page (7-tab view).
 *
 * Insights requires superuser privileges, so we use adminToken.
 */

import { test, expect } from "../../fixtures/auth.fixture";

import {
  DashInsightsPage,
  type InsightsTabLabel,
} from "../../pages/dashboard/insights.page";

test.describe("Dashboard insights", () => {
  let insightsPage: DashInsightsPage;

  test.beforeEach(async ({ page, adminToken }) => {
    insightsPage = new DashInsightsPage(page);
    await insightsPage.gotoWithToken(adminToken);
  });

  test("insights page loads with tabs", async () => {
    await expect(insightsPage.tabs).toBeVisible({
      timeout: 15_000,
    });
    const tabCount =
      await insightsPage.tabButtons.count();
    expect(tabCount).toBe(7);
  });

  test("screener tab is active by default", async () => {
    const label = await insightsPage.activeTabLabel();
    expect(label).toContain("Screener");
  });

  test("each tab loads content without error", async ({
    page,
  }) => {
    const tabs: InsightsTabLabel[] = [
      "Price Targets",
      "Dividends",
      "Risk Metrics",
      "Sectors",
      "Correlation",
      "Quarterly",
    ];

    for (const tab of tabs) {
      await insightsPage.clickTab(tab);
      // Should not show a callback error
      const err = page.locator("text=Callback error");
      expect(await err.count()).toBe(0);
    }
  });

  test("correlation tab renders heatmap chart", async ({
    page,
  }) => {
    test.slow(); // correlation heatmap can be slow
    await insightsPage.clickTab("Correlation");
    // Wait for active tab pane to have a Plotly chart
    const activePane = page.locator(
      ".tab-pane.active .js-plotly-plot, " +
      ".tab-content .js-plotly-plot:visible",
    );
    await expect(activePane.first()).toBeVisible({
      timeout: 60_000,
    });
  });

  test("sectors tab renders chart", async ({ page }) => {
    test.slow(); // chart rendering can be slow
    await insightsPage.clickTab("Sectors");
    const activePane = page.locator(
      ".tab-pane.active .js-plotly-plot, " +
      ".tab-content .js-plotly-plot:visible",
    );
    await expect(activePane.first()).toBeVisible({
      timeout: 60_000,
    });
  });
});
