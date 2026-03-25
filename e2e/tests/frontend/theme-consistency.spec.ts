/**
 * E2E tests for theme consistency across charts.
 *
 * Verifies that TradingView and Plotly charts respect the
 * current light/dark theme, and that theme changes propagate
 * in real-time.  This is the critical test file that catches
 * the bug where light mode renders dark-themed charts.
 *
 * Uses pre-authenticated storageState (superuser) with seeded
 * portfolio data for chart rendering.
 */

import { test, expect } from "../../fixtures/portfolio.fixture";
import { AnalyticsPage } from "../../pages/frontend/analytics.page";
import { InsightsPage } from "../../pages/frontend/insights.page";
import {
  waitForTradingViewChart,
  waitForPlotlyChart,
} from "../../utils/wait.helper";

/** Toggle the theme to dark mode if not already dark. */
async function ensureDarkMode(
  page: import("@playwright/test").Page,
) {
  await page
    .getByTestId("sidebar-theme-toggle")
    .waitFor({ state: "visible", timeout: 10_000 });
  const cls =
    (await page.locator("html").getAttribute("class")) ||
    "";
  if (!cls.includes("dark")) {
    await page
      .getByTestId("sidebar-theme-toggle")
      .click();
    await page.waitForTimeout(500);
  }
  await expect(page.locator("html")).toHaveClass(/dark/, {
    timeout: 5000,
  });
}

/** Toggle the theme to light mode if not already light. */
async function ensureLightMode(
  page: import("@playwright/test").Page,
) {
  await page
    .getByTestId("sidebar-theme-toggle")
    .waitFor({ state: "visible", timeout: 10_000 });
  const cls =
    (await page.locator("html").getAttribute("class")) ||
    "";
  if (cls.includes("dark")) {
    await page
      .getByTestId("sidebar-theme-toggle")
      .click();
    await page.waitForTimeout(500);
  }
  await expect(
    page.locator("html"),
  ).not.toHaveClass(/dark/, { timeout: 5000 });
}

test.describe("Theme consistency across charts", () => {
  test("light mode: TradingView chart has light background", async ({
    page,
    seededPortfolio,
  }) => {
    const analytics = new AnalyticsPage(page);
    await analytics.gotoAnalysis();
    await ensureLightMode(page);
    await analytics.clickTab("portfolio");
    await waitForTradingViewChart(
      page,
      "portfolio-analysis-chart",
    );
    await page.waitForTimeout(1000);

    const chart = analytics.portfolioChartContainer();
    await expect(chart).toHaveScreenshot(
      "tv-chart-light.png",
    );
  });

  test("dark mode: TradingView chart has dark background", async ({
    page,
    seededPortfolio,
  }) => {
    const analytics = new AnalyticsPage(page);
    await analytics.gotoAnalysis();
    await ensureDarkMode(page);
    await analytics.clickTab("portfolio");
    await waitForTradingViewChart(
      page,
      "portfolio-analysis-chart",
    );
    await page.waitForTimeout(1500);

    const chart = analytics.portfolioChartContainer();
    await expect(chart).toHaveScreenshot(
      "tv-chart-dark.png",
    );
  });

  test("toggling theme updates chart in real-time", async ({
    page,
    seededPortfolio,
  }) => {
    const analytics = new AnalyticsPage(page);
    await analytics.gotoAnalysis();
    await analytics.clickTab("portfolio");
    await waitForTradingViewChart(
      page,
      "portfolio-analysis-chart",
    );
    await page.waitForTimeout(1000);

    // Take light screenshot
    const chart = analytics.portfolioChartContainer();
    const lightShot = await chart.screenshot();

    // Toggle to dark
    await page
      .getByTestId("sidebar-theme-toggle")
      .click();
    await page.waitForTimeout(1500);

    // Take dark screenshot — should be visually different
    const darkShot = await chart.screenshot();

    // Buffers should differ (light vs dark backgrounds)
    expect(Buffer.compare(lightShot, darkShot)).not.toBe(
      0,
    );
  });

  test("light mode: Plotly chart uses light theme", async ({
    page,
    seededPortfolio,
  }) => {
    const insights = new InsightsPage(page);
    await insights.gotoInsights();
    await ensureLightMode(page);
    await insights.clickTab("sectors");
    await waitForPlotlyChart(page, "insights-chart");
    await page.waitForTimeout(1000);

    const chart = page.getByTestId("insights-chart");
    await expect(chart).toHaveScreenshot(
      "plotly-chart-light.png",
    );
  });

  test("dark mode: Plotly chart uses dark theme", async ({
    page,
    seededPortfolio,
  }) => {
    const insights = new InsightsPage(page);
    await insights.gotoInsights();
    await ensureDarkMode(page);
    await insights.clickTab("sectors");
    await waitForPlotlyChart(page, "insights-chart");
    await page.waitForTimeout(1500);

    const chart = page.getByTestId("insights-chart");
    await expect(chart).toHaveScreenshot(
      "plotly-chart-dark.png",
    );
  });

  test("theme persists across page navigation", async ({
    page,
    seededPortfolio,
  }) => {
    // Navigate to a page first so the sidebar is visible
    await page.goto("/analytics/analysis");
    await page.waitForLoadState("domcontentloaded");
    await ensureDarkMode(page);

    // Navigate to a different page
    await page.goto("/analytics/marketplace");
    await page.waitForLoadState("domcontentloaded");

    // Dark mode should persist
    await expect(page.locator("html")).toHaveClass(
      /dark/,
      { timeout: 5000 },
    );

    // Navigate back
    await page.goto("/analytics/analysis");
    await page.waitForLoadState("domcontentloaded");
    await expect(page.locator("html")).toHaveClass(
      /dark/,
      { timeout: 5000 },
    );
  });

  test("theme persists across page reload", async ({
    page,
    seededPortfolio,
  }) => {
    // Navigate to a page first so the sidebar is visible
    await page.goto("/analytics/analysis");
    await page.waitForLoadState("domcontentloaded");
    await ensureDarkMode(page);

    await page.reload();
    await page.waitForLoadState("domcontentloaded");

    // Dark mode should persist after reload
    await expect(page.locator("html")).toHaveClass(
      /dark/,
      { timeout: 5000 },
    );
  });

  test("portfolio forecast chart matches theme (regression)", async ({
    page,
    seededPortfolio,
  }) => {
    // THIS IS THE KEY TEST that catches the bug where
    // light mode renders a dark chart background.
    const analytics = new AnalyticsPage(page);
    await analytics.gotoAnalysis();
    await ensureLightMode(page);
    await analytics.clickTab("portfolio-forecast");
    await waitForTradingViewChart(
      page,
      "portfolio-forecast-chart",
    );
    await page.waitForTimeout(1500);

    const chart = analytics.forecastChartContainer();
    await expect(chart).toHaveScreenshot(
      "portfolio-forecast-light-regression.png",
    );
  });
});
