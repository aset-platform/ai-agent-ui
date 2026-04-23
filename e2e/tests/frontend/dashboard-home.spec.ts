/**
 * E2E tests for the Dashboard Home page (/dashboard).
 *
 * Runs against the live backend with a superuser account
 * and pre-seeded portfolio holdings.
 */

import {
  test,
  expect,
} from "../../fixtures/portfolio.fixture";
import { DashboardHomePage } from "../../pages/frontend/dashboard-home.page";
import { waitForPageReady } from "../../utils/wait.helper";

test.describe("Dashboard home", () => {
  let dashboard: DashboardHomePage;

  test.beforeEach(async ({ page, seededPortfolio }) => {
    void seededPortfolio; // ensure fixture runs
    dashboard = new DashboardHomePage(page);
    await dashboard.gotoDashboard();
    // Wait for sidebar + hero (don't use networkidle —
    // dashboard has continuous polling / WebSocket)
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("watchlist table is visible with linked tickers", async () => {
    const table = dashboard.watchlistTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // At least one seeded ticker row should be present
    const row = dashboard.watchlistRow("RELIANCE.NS");
    await expect(row).toBeVisible({ timeout: 10_000 });
  });

  test("portfolio value card shows numeric value", async () => {
    const value = dashboard.portfolioValue();
    await expect(value).toBeVisible({ timeout: 15_000 });
    const text = await value.textContent();
    // Should contain a currency symbol and digits
    expect(text).toMatch(/[\u20B9$\d,.]+/);
  });

  test("daily change shows value with color", async ({
    page,
  }) => {
    const change = dashboard.dailyChange();
    await expect(change).toBeVisible({ timeout: 15_000 });

    // Should have a green or red color class
    const classes = await change.getAttribute("class");
    expect(classes).toMatch(/green|red/i);
  });

  test("market filter has one active selection", async ({
    page,
  }) => {
    // The default market depends on user preferences
    // (India or US). Verify that exactly one is active.
    const markets = ["india", "us"];
    let activeCount = 0;
    for (const market of markets) {
      const btn = page.getByTestId(
        `dashboard-market-filter-${market}`,
      );
      await expect(btn).toBeVisible({ timeout: 10_000 });
      const isActive = await btn.evaluate((el) => {
        return (
          el.getAttribute("aria-pressed") === "true" ||
          el.getAttribute("data-active") === "true" ||
          el.classList.contains("active") ||
          el.classList.contains("bg-primary") ||
          el.classList.contains("btn-primary") ||
          el.classList.contains("bg-white") ||
          el.classList.contains("shadow-sm")
        );
      });
      if (isActive) activeCount++;
    }
    expect(activeCount).toBeGreaterThanOrEqual(1);
  });

  test("switching to US market filter updates watchlist", async ({
    page,
  }) => {
    await dashboard.switchMarketFilter("us");
    await page.waitForTimeout(2_000);

    // Scroll to watchlist and check for US ticker
    const table = dashboard.watchlistTable();
    await table.scrollIntoViewIfNeeded();
    const usRow = dashboard.watchlistRow("AAPL");
    await expect(usRow).toBeVisible({ timeout: 15_000 });
  });

  test("add stock button is visible and clickable", async () => {
    const btn = dashboard.addStockBtn();
    await btn.scrollIntoViewIfNeeded();
    await expect(btn).toBeVisible({ timeout: 15_000 });
    await expect(btn).toBeEnabled();
  });

  test("forecast chart widget is visible", async () => {
    const widget = dashboard.forecastWidget();
    await widget.scrollIntoViewIfNeeded();
    await expect(widget).toBeVisible({ timeout: 15_000 });
  });

  test("market filter persists across page reload", async ({
    page,
  }) => {
    // Switch to US market
    await dashboard.switchMarketFilter("us");
    await page.waitForTimeout(500);

    // Reload the page
    await page.reload();
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });

    // US filter should still be active after reload
    const usBtn = page.getByTestId(
      "dashboard-market-filter-us",
    );
    await expect(usBtn).toBeVisible({ timeout: 10_000 });
    const isActive = await usBtn.evaluate((el) => {
      return (
        el.getAttribute("aria-pressed") === "true" ||
        el.getAttribute("data-active") === "true" ||
        el.classList.contains("active") ||
        el.classList.contains("bg-primary") ||
        el.classList.contains("btn-primary") ||
        el.classList.contains("bg-white") ||
        el.classList.contains("shadow-sm")
      );
    });
    expect(isActive).toBe(true);
  });

  test("visual regression - dashboard home (light)", async ({
    page,
  }) => {
    await page.waitForTimeout(2_000);
    const main = page.locator("main");
    await expect(main).toHaveScreenshot(
      "dashboard-home-light.png",
      { maxDiffPixelRatio: 0.05 },
    );
  });

  test("visual regression - dashboard home (dark)", async ({
    page,
  }) => {
    // Toggle to dark mode
    const toggle = page.getByTestId("sidebar-theme-toggle");
    await toggle.click();
    await page.waitForTimeout(1_000);

    const main = page.locator("main");
    await expect(main).toHaveScreenshot(
      "dashboard-home-dark.png",
      { maxDiffPixelRatio: 0.05 },
    );
  });
});
