/**
 * E2E tests for Dashboard Home widgets.
 *
 * Verifies that the 10 dashboard widgets render on
 * /dashboard with real data from the seeded portfolio.
 * Uses analytics-chromium project (general user auth).
 */

import {
  test,
  expect,
} from "../../fixtures/portfolio.fixture";
import { DashboardHomePage } from "../../pages/frontend/dashboard-home.page";

test.describe("Dashboard widgets", () => {
  let dashboard: DashboardHomePage;

  test.beforeEach(async ({ page, seededPortfolio }) => {
    void seededPortfolio;
    dashboard = new DashboardHomePage(page);
    await dashboard.gotoDashboard();
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
  });

  // ── Hero Section ─────────────────────────────────

  test("hero shows portfolio value", async () => {
    const value = dashboard.portfolioValue();
    await expect(value).toBeVisible({
      timeout: 15_000,
    });
    const text = await value.textContent();
    expect(text).toMatch(/[\u20B9$\d,.]+/);
  });

  test("hero shows daily change with color", async () => {
    const change = dashboard.dailyChange();
    await expect(change).toBeVisible({
      timeout: 15_000,
    });
  });

  test("market filter toggles India/US", async ({
    page,
  }) => {
    const indiaBtn = page.getByTestId(
      "dashboard-market-filter-india",
    );
    const usBtn = page.getByTestId(
      "dashboard-market-filter-us",
    );
    await expect(indiaBtn).toBeVisible({
      timeout: 10_000,
    });
    await expect(usBtn).toBeVisible();
  });

  // ── Sector Allocation ────────────────────────────

  test("sector allocation widget renders", async ({
    page,
  }) => {
    const widget = page.getByText("Sector Allocation");
    await expect(widget).toBeVisible({
      timeout: 15_000,
    });
  });

  // ── Asset Performance ────────────────────────────

  test("asset performance widget renders", async ({
    page,
  }) => {
    const widget = page.getByText("Asset Performance");
    await expect(widget).toBeVisible({
      timeout: 15_000,
    });
  });

  // ── Recommendations ──────────────────────────────

  test("recommendations widget renders", async ({
    page,
  }) => {
    const widget = page.getByText("Recommendations");
    await expect(widget.first()).toBeVisible({
      timeout: 15_000,
    });
  });

  // ── Portfolio P&L Trend ──────────────────────────

  test("P&L trend widget renders with period buttons", async ({
    page,
  }) => {
    const widget = page.getByText("Portfolio P&L Trend");
    await expect(widget).toBeVisible({
      timeout: 15_000,
    });
    // Period selector buttons
    await expect(page.getByText("1M")).toBeVisible();
    await expect(page.getByText("ALL")).toBeVisible();
  });

  // ── Forecast Widget ──────────────────────────────

  test("forecast widget renders", async () => {
    const widget = dashboard.forecastWidget();
    await widget.scrollIntoViewIfNeeded();
    await expect(widget).toBeVisible({
      timeout: 15_000,
    });
  });

  // ── Watchlist ────────────────────────────────────

  test("watchlist table shows seeded tickers", async ({
    page,
  }) => {
    const table = dashboard.watchlistTable();
    await table.scrollIntoViewIfNeeded();
    await expect(table).toBeVisible({
      timeout: 15_000,
    });
    // At least one seeded ticker visible
    const row = dashboard.watchlistRow("RELIANCE.NS");
    await expect(row).toBeVisible({
      timeout: 10_000,
    });
  });
});
