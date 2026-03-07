/**
 * E2E tests for the Dash forecast page.
 */

import { test, expect } from "../../fixtures/auth.fixture";

import { DashForecastPage } from "../../pages/dashboard/forecast.page";

test.describe("Dashboard forecast", () => {
  let forecastPage: DashForecastPage;

  test.beforeEach(async ({ page, userToken }) => {
    forecastPage = new DashForecastPage(page);
    await forecastPage.gotoWithToken(userToken);
  });

  test("forecast page loads with ticker dropdown", async () => {
    await expect(forecastPage.tickerDropdown).toBeVisible();
  });

  test("select ticker → forecast chart renders", async ({
    page,
  }) => {
    await forecastPage.selectTicker("RELIANCE.NS");
    // Chart should render after data loads
    const chart = page.locator(".js-plotly-plot").first();
    await expect(chart).toBeVisible({ timeout: 30_000 });
  });

  test("no ticker selected → warning on refresh", async ({
    page,
  }) => {
    await expect(forecastPage.refreshBtn).toBeVisible({
      timeout: 15_000,
    });
    await forecastPage.refreshBtn.click();
    // Should show a warning (select a ticker)
    const status = page.locator("#forecast-refresh-status");
    await expect(status).toContainText("Select a ticker", {
      timeout: 10_000,
    });
  });

  test("refresh generates accuracy metrics", async ({
    page,
  }) => {
    test.slow(); // allow 3x the default timeout
    await forecastPage.selectTicker("RELIANCE.NS");
    await expect(forecastPage.refreshBtn).toBeVisible({
      timeout: 15_000,
    });
    await forecastPage.refreshBtn.click();
    // Accuracy row should populate after forecast completes
    const status = page.locator("#forecast-refresh-status");
    await expect(status).not.toBeEmpty({ timeout: 90_000 });
  });
});
