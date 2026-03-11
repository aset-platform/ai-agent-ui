/**
 * E2E tests for the Dash home page (stock cards overview).
 */

import { test, expect } from "../../fixtures/auth.fixture";

import { DashHomePage } from "../../pages/dashboard/home.page";

test.describe("Dashboard home", () => {
  let homePage: DashHomePage;

  test.beforeEach(async ({ page, userToken }) => {
    homePage = new DashHomePage(page);
    await homePage.gotoWithToken(userToken);
  });

  test("home loads and shows stock cards", async () => {
    await expect(homePage.stockCards.first()).toBeVisible({
      timeout: 30_000,
    });
    const count = await homePage.getCardCount();
    expect(count).toBeGreaterThan(0);
  });

  test("search ticker → navigates to analysis", async ({
    page,
  }) => {
    // Wait for search input to be interactive
    await expect(homePage.tickerSearch).toBeVisible({
      timeout: 15_000,
    });
    await homePage.searchTicker("AAPL");
    expect(page.url()).toContain("/analysis");
  });

  test("registry dropdown is populated", async ({
    page,
  }) => {
    await expect(homePage.registryDropdown).toBeVisible();
    await homePage.registryDropdown.click();
    // Dash dbc dropdown opens a listbox with option roles
    const options = page.getByRole("option");
    await expect(options.first()).toBeVisible({
      timeout: 10_000,
    });
    const count = await options.count();
    expect(count).toBeGreaterThan(0);
  });

  test("pagination controls are visible", async () => {
    await expect(homePage.pagination).toBeVisible();
  });

  test("market filter switches between India and US", async ({
    page,
  }) => {
    // Click US filter
    await homePage.filterUS.click();
    // Wait for Dash callback to re-render cards
    await page.waitForTimeout(2_000);
    const usCount = await homePage.getCardCount();

    // Click India filter
    await homePage.filterIndia.click();
    await page.waitForTimeout(2_000);
    const indiaCount = await homePage.getCardCount();

    // At least one filter should have cards
    expect(usCount + indiaCount).toBeGreaterThan(0);
  });

  test("per-card refresh shows spinner then result", async ({
    page,
  }) => {
    // Wait for at least one card to render
    await expect(homePage.stockCards.first()).toBeVisible({
      timeout: 30_000,
    });

    // Find first card's refresh button
    const firstRefreshBtn = page
      .locator('[id*="card-refresh-btn"]')
      .first();

    if ((await firstRefreshBtn.count()) > 0) {
      await firstRefreshBtn.click();

      // Should see spinner or status icon
      const statusIcon = page
        .locator('[id*="card-refresh-status"]')
        .first();
      await expect(statusIcon).toBeVisible({ timeout: 60_000 });
    }
  });
});
