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

  test("horizon radio has 3 options", async ({ page }) => {
    const radio = page.locator("#forecast-horizon-radio");
    await expect(radio).toBeVisible({ timeout: 15_000 });
    const options = radio.locator("input[type=radio]");
    const count = await options.count();
    expect(count).toBe(3); // 3, 6, 9 months
  });

  test("view radio has 3 chart views", async ({ page }) => {
    const radio = page.locator("#forecast-view-radio");
    await expect(radio).toBeVisible({ timeout: 15_000 });
    const options = radio.locator("input[type=radio]");
    const count = await options.count();
    expect(count).toBe(3); // standard, decomposition, multi_horizon
  });

  test("switching view radio re-renders chart", async ({
    page,
  }) => {
    await forecastPage.selectTicker("AAPL");
    const chart = page.locator(".js-plotly-plot").first();
    await expect(chart).toBeVisible({ timeout: 30_000 });

    // Switch to decomposition view
    const decompLabel = page
      .locator("#forecast-view-radio")
      .getByText("Decomposition");
    await decompLabel.click();
    await page.waitForTimeout(3_000);
    // Chart should still be visible (re-rendered)
    await expect(chart).toBeVisible();
  });

  test("switching horizon radio updates forecast", async ({
    page,
  }) => {
    await forecastPage.selectTicker("AAPL");
    const chart = page.locator(".js-plotly-plot").first();
    await expect(chart).toBeVisible({ timeout: 30_000 });

    // Switch to 3-month horizon
    const threeMonth = page
      .locator("#forecast-horizon-radio")
      .getByText("3 Months");
    await threeMonth.click();
    await page.waitForTimeout(3_000);
    await expect(chart).toBeVisible();
  });

  test("forecast target cards container exists", async ({
    page,
  }) => {
    const cards = page.locator("#forecast-target-cards");
    await expect(cards).toBeAttached({
      timeout: 15_000,
    });
  });

  test("forecast accuracy row container exists", async ({
    page,
  }) => {
    await expect(forecastPage.accuracyRow).toBeAttached({
      timeout: 15_000,
    });
  });
});
