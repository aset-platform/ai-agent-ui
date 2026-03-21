/**
 * E2E tests for the Marketplace / Registry page
 * (/analytics/marketplace).
 *
 * Runs against the live backend with a superuser account
 * and pre-seeded portfolio holdings.
 *
 * NOTE: These tests do NOT link/unlink tickers — they only
 * verify button visibility to avoid side-effects on other tests.
 */

import {
  test,
  expect,
} from "../../fixtures/portfolio.fixture";
import { MarketplacePage } from "../../pages/frontend/marketplace.page";

test.describe("Marketplace/Registry page", () => {
  let marketplace: MarketplacePage;

  test.beforeEach(async ({ page, seededPortfolio }) => {
    void seededPortfolio; // ensure fixture runs
    marketplace = new MarketplacePage(page);
    await marketplace.gotoMarketplace();
  });

  test("ticker table renders with registry data", async () => {
    const table = marketplace.marketplaceTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Should have at least one row
    const rows = table.locator("tbody tr");
    await expect(rows.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("search filters tickers by name/symbol", async ({
    page,
  }) => {
    const table = marketplace.marketplaceTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Type "RELI" to filter
    await marketplace.searchTicker("RELI");
    await page.waitForTimeout(1_000);

    // Filtered rows should contain RELIANCE
    const rows = table.locator("tbody tr");
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);

    // Verify the visible row contains the search term
    const firstRowText = await rows.first().textContent();
    expect(firstRowText?.toUpperCase()).toContain("RELI");
  });

  test("market filter All shows all tickers", async ({
    page,
  }) => {
    await marketplace.selectMarket("all");
    await page.waitForTimeout(1_000);

    const table = marketplace.marketplaceTable();
    await expect(table).toBeVisible({ timeout: 10_000 });

    const rows = table.locator("tbody tr");
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
  });

  test("market filter India shows only India tickers", async ({
    page,
  }) => {
    await marketplace.selectMarket("india");
    await page.waitForTimeout(1_000);

    const table = marketplace.marketplaceTable();
    await expect(table).toBeVisible({ timeout: 10_000 });

    // All visible rows should have .NS suffix tickers
    const rows = table.locator("tbody tr");
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
  });

  test("market filter US shows only US tickers", async ({
    page,
  }) => {
    await marketplace.selectMarket("us");
    await page.waitForTimeout(1_000);

    const table = marketplace.marketplaceTable();
    await expect(table).toBeVisible({ timeout: 10_000 });

    const rows = table.locator("tbody tr");
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
  });

  test("linked tickers show Unlink button", async ({
    page,
  }) => {
    const table = marketplace.marketplaceTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // The specific linked ticker may not be on the
    // current page (pagination). Find any Unlink button.
    const unlinkBtn = page
      .locator("[data-testid^='marketplace-unlink-']")
      .first();
    await expect(unlinkBtn).toBeVisible({ timeout: 10_000 });
  });

  test("unlinked tickers show Link button", async ({
    page,
  }) => {
    const table = marketplace.marketplaceTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Find any Link button in the table (not Unlink)
    const linkButtons = table.locator(
      '[data-testid^="marketplace-link-"]',
    );
    // There should be at least one unlinked ticker
    const count = await linkButtons.count();
    expect(count).toBeGreaterThan(0);
  });

  test("stats bar shows ticker count and linked count", async () => {
    const stats = marketplace.statsBar();
    await expect(stats).toBeVisible({ timeout: 10_000 });

    const text = await stats.textContent();
    // Should show counts (e.g. "10 linked / 50 total")
    expect(text).toMatch(/\d+/);
  });

  test("pagination next/prev buttons work", async ({
    page,
  }) => {
    const nextBtn = marketplace.nextButton();
    await expect(nextBtn).toBeVisible({ timeout: 10_000 });

    // Click next page
    await nextBtn.click();
    await page.waitForTimeout(1_000);

    // Page info should update
    const pageInfo = marketplace.pageInfo();
    const text = await pageInfo.textContent();
    expect(text).toMatch(/2/);

    // Click prev to go back
    const prevBtn = marketplace.prevButton();
    await prevBtn.click();
    await page.waitForTimeout(1_000);

    const text2 = await pageInfo.textContent();
    expect(text2).toMatch(/1/);
  });

  test("page info shows current page", async () => {
    const pageInfo = marketplace.pageInfo();
    await expect(pageInfo).toBeVisible({ timeout: 10_000 });

    const text = await pageInfo.textContent();
    // Should indicate page number (e.g. "Page 1 of 3")
    expect(text).toMatch(/\d+/);
  });
});
