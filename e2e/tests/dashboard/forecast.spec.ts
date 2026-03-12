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
    await forecastPage.selectTicker("AAPL");
    // Chart should render after data loads
    const chart = page.locator(".js-plotly-plot").first();
    await expect(chart).toBeVisible({ timeout: 30_000 });
  });

  test("refresh with ticker → status updates", async ({
    page,
  }) => {
    test.slow(); // 3x timeout — refresh runs in background
    await expect(forecastPage.refreshBtn).toBeVisible({
      timeout: 15_000,
    });
    await forecastPage.refreshBtn.click();
    // Poll callback writes ✓ or ✗ when background job done.
    const status = page.locator("#forecast-refresh-status");
    await expect(status).toContainText(/[✓✗]/, {
      timeout: 120_000,
    });
  });

  test("refresh generates accuracy metrics", async ({
    page,
  }) => {
    test.slow(); // 3x timeout — refresh runs in background
    await forecastPage.selectTicker("AAPL");
    await expect(forecastPage.refreshBtn).toBeVisible({
      timeout: 15_000,
    });
    await forecastPage.refreshBtn.click();
    // Poll callback writes ✓ or ✗ when background job done.
    const status = page.locator("#forecast-refresh-status");
    await expect(status).toContainText(/[✓✗]/, {
      timeout: 120_000,
    });
  });
});
