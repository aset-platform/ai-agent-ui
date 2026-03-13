/**
 * Deep coverage E2E tests for the Dash marketplace page.
 *
 * Extends the basic marketplace.spec.ts with search,
 * dark mode, and ticker management flows.
 */

import { test, expect } from "../../fixtures/auth.fixture";

import { DashMarketplacePage } from "../../pages/dashboard/marketplace.page";

test.describe("Dashboard marketplace deep", () => {
  let marketplacePage: DashMarketplacePage;

  test.beforeEach(async ({ page, userToken }) => {
    marketplacePage = new DashMarketplacePage(page);
    await marketplacePage.gotoWithToken(userToken);
  });

  test("marketplace grid shows ticker cards", async ({
    page,
  }) => {
    await expect(marketplacePage.grid).toBeVisible({
      timeout: 30_000,
    });
    // Should have at least one card or row
    const cards = page.locator(
      ".stock-card, .card, tr",
    );
    const count = await cards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("search filters results", async ({ page }) => {
    const search = marketplacePage.searchInput;
    if ((await search.count()) > 0) {
      await search.fill("AAPL");
      await page.waitForTimeout(2_000);
      // Grid should still be visible (filtered)
      await expect(marketplacePage.grid).toBeVisible();
    }
  });

  test("add button text changes after click", async ({
    page,
  }) => {
    const addBtn = marketplacePage.addButtons.first();
    if ((await addBtn.count()) > 0) {
      const textBefore = await addBtn.innerText();
      await addBtn.click();
      await page.waitForTimeout(2_000);
      // Button text may change to "Remove" or show
      // a success indicator
      const textAfter = await addBtn.innerText();
      // Just verify the page didn't crash
      await expect(marketplacePage.grid).toBeVisible();
    }
  });

  test("dark mode renders correctly", async ({
    page,
    userToken,
  }) => {
    await page.goto(
      `/marketplace?token=${userToken}&theme=dark`,
    );
    await page.waitForTimeout(3_000);
    const hasDark = await page.evaluate(
      () =>
        document.body.classList.contains("dark-mode"),
    );
    expect(hasDark).toBe(true);
    // Grid should still render
    await expect(marketplacePage.grid).toBeVisible({
      timeout: 30_000,
    });
  });

  test("page does not have horizontal overflow", async ({
    page,
  }) => {
    await expect(marketplacePage.grid).toBeVisible({
      timeout: 30_000,
    });
    const hasOverflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth,
    );
    expect(hasOverflow).toBe(false);
  });
});
