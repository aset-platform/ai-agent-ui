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

  test("screener tab has filter controls", async ({
    page,
  }) => {
    // RSI filter
    const rsiFilter = page.locator("#screener-rsi-filter");
    await expect(rsiFilter).toBeVisible({
      timeout: 15_000,
    });
    // Market filter
    const marketFilter = page.locator(
      "#screener-market-filter",
    );
    await expect(marketFilter).toBeVisible();
    // Sector filter
    const sectorFilter = page.locator(
      "#screener-sector-filter",
    );
    await expect(sectorFilter).toBeVisible();
  });

  test("screener table has sortable headers", async ({
    page,
  }) => {
    await expect(
      page.locator("#screener-table-container table"),
    ).toBeVisible({ timeout: 30_000 });
    const sortBtns = page.locator(
      "#screener-table-container .sort-header-btn",
    );
    const count = await sortBtns.count();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  test("screener pagination and count text visible", async ({
    page,
  }) => {
    await expect(
      page.locator("#screener-table-container table"),
    ).toBeVisible({ timeout: 30_000 });
    const pagination = page.locator(
      "#screener-pagination",
    );
    await expect(pagination).toBeVisible();
    const countText = page.locator(
      "#screener-count-text",
    );
    await expect(countText).toBeVisible();
    const text = await countText.innerText();
    expect(text).toMatch(/\d+ stocks?/);
  });

  test("screener page-size selector works", async ({
    page,
  }) => {
    await expect(
      page.locator("#screener-table-container table"),
    ).toBeVisible({ timeout: 30_000 });
    const pageSize = page.locator("#screener-page-size");
    await expect(pageSize).toBeVisible();
    await pageSize.selectOption("25");
    await page.waitForTimeout(2_000);
    // Table should still be visible after size change
    await expect(
      page.locator("#screener-table-container table"),
    ).toBeVisible();
  });

  test("screener RSI filter changes results", async ({
    page,
  }) => {
    await expect(
      page.locator("#screener-table-container"),
    ).toBeVisible({ timeout: 30_000 });
    const rsiFilter = page.locator("#screener-rsi-filter");
    await rsiFilter.selectOption("oversold");
    await page.waitForTimeout(2_000);
    // Should show filtered results or empty message
    await expect(
      page.locator("#screener-table-container"),
    ).toBeVisible();
  });

  test("price targets tab has filters and table", async ({
    page,
  }) => {
    await insightsPage.clickTab("Price Targets");
    await expect(
      page.locator("#targets-table-container"),
    ).toBeVisible({ timeout: 30_000 });
    // Ticker dropdown
    await expect(
      page.locator("#targets-ticker-dropdown"),
    ).toBeVisible();
    // Pagination
    await expect(
      page.locator("#targets-pagination"),
    ).toBeVisible();
  });

  test("dividends tab has filters and table", async ({
    page,
  }) => {
    await insightsPage.clickTab("Dividends");
    await expect(
      page.locator("#dividends-table-container"),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.locator("#dividends-ticker-dropdown"),
    ).toBeVisible();
    await expect(
      page.locator("#dividends-pagination"),
    ).toBeVisible();
  });

  test("risk tab has table with pagination", async ({
    page,
  }) => {
    await insightsPage.clickTab("Risk Metrics");
    await expect(
      page.locator("#risk-table-container"),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.locator("#risk-pagination"),
    ).toBeVisible();
  });

  test("quarterly tab has filters and table", async ({
    page,
  }) => {
    await insightsPage.clickTab("Quarterly");
    await expect(
      page.locator("#quarterly-table-container"),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.locator("#quarterly-ticker-filter"),
    ).toBeVisible();
    await expect(
      page.locator("#quarterly-pagination"),
    ).toBeVisible();
  });
});
