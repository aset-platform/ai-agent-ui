/**
 * E2E tests for analytics tab navigation.
 *
 * Verifies that all 5 tabs are visible, switchable, and retain
 * correct active state across navigation.
 */

import { test, expect } from "../../fixtures/portfolio.fixture";
import { AnalyticsPage } from "../../pages/frontend/analytics.page";

test.describe("Analytics tab navigation", () => {
  let analytics: AnalyticsPage;

  test.beforeEach(
    async ({ page, seededPortfolio: _seeded }) => {
      analytics = new AnalyticsPage(page);
      await analytics.gotoAnalysis();
    },
  );

  test("all 5 tabs are visible", async ({ page }) => {
    const tabs = [
      "portfolio",
      "portfolio-forecast",
      "analysis",
      "forecast",
      "compare",
    ];
    for (const tab of tabs) {
      await expect(
        page.getByTestId(`analytics-tab-${tab}`),
      ).toBeVisible({ timeout: 10_000 });
    }
  });

  test("a tab is active by default", async () => {
    const activeText = await analytics.activeTabText();
    // The default tab comes from user preferences;
    // just verify that one of the 5 tabs is active.
    const validTabs = [
      "Portfolio Analysis",
      "Portfolio Forecast",
      "Stock Analysis",
      "Stock Forecast",
      "Compare Stocks",
    ];
    expect(
      validTabs.some((t) => activeText.includes(t)),
    ).toBeTruthy();
  });

  test("clicking Portfolio Forecast tab switches content", async ({
    page,
  }) => {
    await analytics.clickTab("portfolio-forecast");
    const activeText = await analytics.activeTabText();
    expect(activeText).toContain("Portfolio Forecast");
    await expect(
      page.getByTestId("portfolio-forecast-chart"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("clicking Stock Analysis tab switches content", async ({
    page,
  }) => {
    await analytics.clickTab("analysis");
    const activeText = await analytics.activeTabText();
    expect(activeText).toContain("Stock Analysis");
    await expect(
      page.getByTestId("stock-analysis-chart"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("clicking Stock Forecast tab switches content", async ({
    page,
  }) => {
    await analytics.clickTab("forecast");
    const activeText = await analytics.activeTabText();
    expect(activeText).toContain("Stock Forecast");
    await expect(
      page.getByTestId("stock-forecast-chart"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("clicking Compare Stocks tab switches content", async ({
    page,
  }) => {
    await analytics.clickTab("compare");
    const activeText = await analytics.activeTabText();
    expect(activeText).toContain("Compare");
    // Compare tab may show chart or empty state
    const chart = page.getByTestId("compare-chart");
    const empty = page.getByTestId("compare-empty");
    await expect(chart.or(empty)).toBeVisible({
      timeout: 15_000,
    });
  });

  test("active tab has correct styling", async ({
    page,
  }) => {
    // Click a known tab first to set a definite state
    await analytics.clickTab("portfolio");
    const portfolioTab = page.getByTestId(
      "analytics-tab-portfolio",
    );
    const portfolioCls =
      (await portfolioTab.getAttribute("class")) || "";
    expect(portfolioCls).toContain("text-indigo-600");

    // Switch tab and verify new tab becomes active
    await analytics.clickTab("forecast");
    const forecastTab = page.getByTestId(
      "analytics-tab-forecast",
    );
    const forecastCls =
      (await forecastTab.getAttribute("class")) || "";
    expect(forecastCls).toContain("text-indigo-600");

    // Previous tab should no longer be active
    const oldCls =
      (await portfolioTab.getAttribute("class")) || "";
    expect(oldCls).not.toContain("text-indigo-600");
  });

  test("navigating away and back preserves active tab", async ({
    page,
  }) => {
    // Switch to Portfolio Forecast tab
    await analytics.clickTab("portfolio-forecast");
    const before = await analytics.activeTabText();
    expect(before).toContain("Portfolio Forecast");

    // Navigate away to dashboard
    await page.goto("/dashboard", { waitUntil: "commit" });
    await page.waitForLoadState("domcontentloaded");

    // Navigate back
    await page.goBack({ waitUntil: "commit" });
    await page.waitForLoadState("domcontentloaded");

    // Verify the forecast tab is still active
    const forecastTab = page.getByTestId(
      "analytics-tab-portfolio-forecast",
    );
    await expect(forecastTab).toBeVisible({ timeout: 10_000 });
    const after = await analytics.activeTabText();
    expect(after).toContain("Portfolio Forecast");
  });
});
