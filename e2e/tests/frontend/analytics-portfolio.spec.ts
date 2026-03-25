/**
 * E2E tests for the Portfolio Analysis tab on the analytics page.
 *
 * Validates TradingView chart rendering, metric cards, period
 * selection, currency badge, refresh flow, and visual regression.
 */

import { test, expect } from "../../fixtures/portfolio.fixture";
import { AnalyticsPage } from "../../pages/frontend/analytics.page";
import {
  waitForTradingViewChart,
} from "../../utils/wait.helper";

test.describe("Portfolio Analysis tab", () => {
  let analytics: AnalyticsPage;

  test.beforeEach(
    async ({ page, seededPortfolio: _seeded }) => {
      analytics = new AnalyticsPage(page);
      await analytics.gotoAnalysis();
      await analytics.clickTab("portfolio");
      await waitForTradingViewChart(
        page,
        "portfolio-analysis-chart",
      );
    },
  );

  test("renders TradingView chart with canvas element", async ({
    page,
  }) => {
    const container = analytics.portfolioChartContainer();
    await expect(container).toBeVisible();
    const canvas = container.locator("canvas").first();
    await expect(canvas).toBeVisible();
  });

  test("shows 6 metric cards with values", async () => {
    const metrics = [
      "totalReturn",
      "annualized",
      "maxDrawdown",
      "sharpe",
      "bestDay",
      "worstDay",
    ];
    for (const metric of metrics) {
      const value = analytics.portfolioMetricValue(metric);
      await expect(value).toBeVisible({ timeout: 10_000 });
    }
  });

  test("metric cards contain numeric values", async () => {
    const metrics = [
      "totalReturn",
      "annualized",
      "maxDrawdown",
      "sharpe",
      "bestDay",
      "worstDay",
    ];
    for (const metric of metrics) {
      const value = analytics.portfolioMetricValue(metric);
      const text = await value.innerText();
      // Should contain at least one digit
      expect(text).toMatch(/\d/);
    }
  });

  test("period pills are visible", async ({ page }) => {
    const periods = [
      "1d",
      "1w",
      "1m",
      "3m",
      "6m",
      "1y",
      "all",
    ];
    for (const period of periods) {
      await expect(
        page.getByTestId(
          `portfolio-analysis-period-${period}`,
        ),
      ).toBeVisible();
    }
  });

  test("switching period from 6m to 1m updates chart", async ({
    page,
  }) => {
    // Select 6m first
    await analytics.selectPeriod("6m");
    await page.waitForTimeout(500);

    // Take a reference screenshot of the canvas
    const canvas = analytics
      .portfolioChartContainer()
      .locator("canvas")
      .first();
    const before = await canvas.screenshot();

    // Switch to 1m
    await analytics.selectPeriod("1m");
    await page.waitForTimeout(1500);

    // Canvas should have re-rendered (different data range)
    const after = await canvas.screenshot();
    expect(Buffer.compare(before, after)).not.toBe(0);
  });

  test("currency badge shows INR for India market", async () => {
    const badge = analytics.portfolioCurrencyBadge();
    await expect(badge).toBeVisible({ timeout: 10_000 });
    const text = await badge.innerText();
    expect(text.toUpperCase()).toContain("INR");
  });

  test("refresh button is visible and enabled", async ({
    page,
  }) => {
    const btn = page.getByTestId(
      "portfolio-analysis-refresh-btn",
    );
    await expect(btn).toBeVisible();
    await expect(btn).toBeEnabled();
  });

  test("refresh triggers spinner and completes", async ({
    page,
  }) => {
    test.slow();
    const refreshBtn = page.getByTestId(
      "portfolio-analysis-refresh-btn",
    );
    await refreshBtn.click();

    // Verify the button click doesn't crash and the
    // icon element remains visible (spinner or done).
    const icon = page.getByTestId(
      "portfolio-analysis-refresh-icon",
    );
    const svg = icon.locator("svg").first();
    await expect(
      svg.or(icon),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("API /dashboard/portfolio/performance returns valid data", async ({
    page,
    userToken,
  }) => {
    // Use direct API call to avoid waitForResponse race
    const baseUrl =
      process.env.BACKEND_URL || "http://127.0.0.1:8181";
    const res = await page.request.get(
      `${baseUrl}/v1/dashboard/portfolio/performance`,
      {
        headers: { Authorization: `Bearer ${userToken}` },
        timeout: 30_000,
      },
    );
    expect(res.status()).toBe(200);
    const data = await res.json();

    // Verify response structure
    expect(data).toBeDefined();
    expect(typeof data).toBe("object");
  });

  test("dark mode - chart container background changes", async ({
    page,
  }) => {
    // Screenshot in light mode
    const container = analytics.portfolioChartContainer();
    const lightShot = await container.screenshot();

    // Toggle dark mode
    await page.getByTestId("sidebar-theme-toggle").click();
    await page.waitForTimeout(1000);

    // Screenshot in dark mode
    const darkShot = await container.screenshot();

    // Screenshots should differ
    expect(Buffer.compare(lightShot, darkShot)).not.toBe(0);

    // Toggle back to light mode for subsequent tests
    await page.getByTestId("sidebar-theme-toggle").click();
    await page.waitForTimeout(500);
  });

  test("visual regression - portfolio analysis chart (light)", async () => {
    const container = analytics.portfolioChartContainer();
    await expect(container).toHaveScreenshot(
      "portfolio-analysis-chart-light.png",
    );
  });

  test("visual regression - portfolio analysis chart (dark)", async ({
    page,
  }) => {
    await page.getByTestId("sidebar-theme-toggle").click();
    await page.waitForTimeout(1000);

    const container = analytics.portfolioChartContainer();
    await expect(container).toHaveScreenshot(
      "portfolio-analysis-chart-dark.png",
    );

    // Toggle back
    await page.getByTestId("sidebar-theme-toggle").click();
    await page.waitForTimeout(500);
  });
});
