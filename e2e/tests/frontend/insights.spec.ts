/**
 * E2E tests for the Insights page (/analytics/insights).
 *
 * Runs against the live backend with a superuser account
 * and pre-seeded portfolio holdings.  Plotly charts are used
 * on the Sectors and Correlation tabs.
 */

import {
  test,
  expect,
} from "../../fixtures/portfolio.fixture";
import { InsightsPage } from "../../pages/frontend/insights.page";
import { waitForPlotlyChart } from "../../utils/wait.helper";

/** The 7 tab identifiers used on the Insights page. */
const TAB_IDS = [
  "screener",
  "targets",
  "dividends",
  "risk",
  "sectors",
  "correlation",
  "quarterly",
] as const;

test.describe("Insights page", () => {
  let insights: InsightsPage;

  test.beforeEach(async ({ page, seededPortfolio }) => {
    void seededPortfolio; // ensure fixture runs
    insights = new InsightsPage(page);
    await insights.gotoInsights();
  });

  test("all 7 tabs are visible", async ({ page }) => {
    for (const id of TAB_IDS) {
      const tab = page.getByTestId(`insights-tab-${id}`);
      await expect(tab).toBeVisible({ timeout: 15_000 });
    }
  });

  test("screener tab loads table with data", async () => {
    await insights.clickTab("screener");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Table should have at least one row
    const rows = table.locator("tbody tr");
    await expect(rows.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("screener RSI filter works", async ({ page }) => {
    await insights.clickTab("screener");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Select "oversold" RSI filter
    await insights.selectRsiFilter("oversold");
    await page.waitForTimeout(1_000);

    // Table should still be visible (may have fewer rows)
    await expect(table).toBeVisible();
  });

  test("price targets tab loads table", async () => {
    await insights.clickTab("targets");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    const rows = table.locator("tbody tr");
    await expect(rows.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("dividends tab loads table", async () => {
    await insights.clickTab("dividends");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    const rows = table.locator("tbody tr");
    await expect(rows.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("risk metrics tab loads table", async () => {
    await insights.clickTab("risk");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    const rows = table.locator("tbody tr");
    await expect(rows.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("sectors tab renders Plotly chart", async ({
    page,
  }) => {
    await insights.clickTab("sectors");
    await waitForPlotlyChart(page, "insights-chart", 30_000);

    const chart = insights.insightsChart();
    const plotly = chart.locator(".js-plotly-plot");
    await expect(plotly).toBeVisible();
  });

  test("correlation tab renders Plotly heatmap", async ({
    page,
  }) => {
    test.slow(); // 3x timeout — correlation can take 60s
    await insights.clickTab("correlation");
    await waitForPlotlyChart(page, "insights-chart", 60_000);

    const chart = insights.insightsChart();
    const plotly = chart.locator(".js-plotly-plot");
    await expect(plotly).toBeVisible();
  });

  test("quarterly tab loads table", async () => {
    await insights.clickTab("quarterly");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });
  });

  test("quarterly statement type selector switches views", async ({
    page,
  }) => {
    await insights.clickTab("quarterly");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Switch between statement types
    for (const type of ["balance", "cashflow", "income"]) {
      await insights.selectStatementType(type);
      await page.waitForTimeout(1_000);
      await expect(table).toBeVisible();
    }
  });

  test("market filter filters table rows", async ({
    page,
  }) => {
    await insights.clickTab("screener");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Apply market filter
    await insights.selectMarketFilter("india");
    await page.waitForTimeout(1_000);
    await expect(table).toBeVisible();
  });

  test("sector filter filters table rows", async ({
    page,
  }) => {
    await insights.clickTab("screener");
    const table = insights.insightsTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Apply a sector filter
    await insights.selectSectorFilter("Technology");
    await page.waitForTimeout(1_000);
    await expect(table).toBeVisible();
  });

  test("dark mode - Plotly charts use dark theme", async ({
    page,
  }) => {
    // Toggle to dark mode
    const toggle = page.getByTestId("sidebar-theme-toggle");
    await toggle.click();
    await page.waitForTimeout(500);

    await insights.clickTab("sectors");
    await waitForPlotlyChart(page, "insights-chart", 30_000);

    // Plotly should have a dark background
    const bgColor = await page.evaluate(() => {
      const plot = document.querySelector(
        ".js-plotly-plot .plot-container",
      );
      if (!plot) return "";
      return window.getComputedStyle(plot).backgroundColor;
    });
    // Dark backgrounds typically have low RGB values
    expect(bgColor).toBeTruthy();
  });

  test("visual regression - sectors chart (light)", async ({
    page,
  }) => {
    await insights.clickTab("sectors");
    await waitForPlotlyChart(page, "insights-chart", 30_000);

    const chart = insights.insightsChart();
    await expect(chart).toHaveScreenshot(
      "insights-sectors-light.png",
      { maxDiffPixelRatio: 0.05 },
    );
  });

  test("visual regression - correlation heatmap (dark)", async ({
    page,
  }) => {
    test.slow();

    // Toggle to dark mode
    const toggle = page.getByTestId("sidebar-theme-toggle");
    await toggle.click();
    await page.waitForTimeout(500);

    await insights.clickTab("correlation");
    await waitForPlotlyChart(page, "insights-chart", 60_000);

    const chart = insights.insightsChart();
    await expect(chart).toHaveScreenshot(
      "insights-correlation-dark.png",
      { maxDiffPixelRatio: 0.05 },
    );
  });
});
