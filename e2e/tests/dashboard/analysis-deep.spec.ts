/**
 * Deep coverage E2E tests for the Dash analysis page.
 *
 * Extends the basic analysis.spec.ts with chart interactions,
 * dark mode, and edge cases.
 */

import { test, expect } from "../../fixtures/auth.fixture";

import { DashAnalysisPage } from "../../pages/dashboard/analysis.page";

test.describe("Dashboard analysis deep", () => {
  let analysisPage: DashAnalysisPage;

  test.beforeEach(async ({ page, userToken }) => {
    analysisPage = new DashAnalysisPage(page);
    await analysisPage.gotoWithToken(userToken);
  });

  test("ticker dropdown has multiple options", async ({
    page,
  }) => {
    await analysisPage.tickerDropdown.click();
    const options = page.getByRole("option");
    await expect(options.first()).toBeVisible({
      timeout: 10_000,
    });
    const count = await options.count();
    expect(count).toBeGreaterThan(1);
  });

  test("tabs are all visible", async () => {
    await expect(analysisPage.tabs).toBeVisible({
      timeout: 15_000,
    });
    // Analysis page has at least 3 tabs
    const tabs = analysisPage.tabs.getByRole("tab");
    const count = await tabs.count();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  test("chart shows plot tools on hover", async ({
    page,
  }) => {
    await analysisPage.selectTicker("AAPL");
    const chart = page
      .locator(".js-plotly-plot")
      .first();
    await expect(chart).toBeVisible({
      timeout: 15_000,
    });
    // Plotly modebar appears on hover
    await chart.hover();
    const modebar = chart.locator(".modebar");
    await expect(modebar).toBeVisible({
      timeout: 5_000,
    });
  });

  test("refresh button is visible", async () => {
    await expect(analysisPage.refreshBtn).toBeVisible({
      timeout: 15_000,
    });
  });

  test("dark mode renders without visual artifacts", async ({
    page,
    userToken,
  }) => {
    // Navigate with dark theme
    await page.goto(
      `/analysis?token=${userToken}&theme=dark`,
    );
    await page.waitForTimeout(3_000);
    // Body should have dark-mode class
    const hasDark = await page.evaluate(
      () =>
        document.body.classList.contains("dark-mode"),
    );
    expect(hasDark).toBe(true);
    // No horizontal overflow
    const hasOverflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth,
    );
    expect(hasOverflow).toBe(false);
  });

  test("compare stocks tab loads", async ({ page }) => {
    await analysisPage.clickTab("Compare Stocks");
    // Should have content, not empty
    const content = page.locator(
      ".tab-pane.active, .tab-content",
    );
    await expect(content.first()).toBeVisible({
      timeout: 15_000,
    });
  });
});
