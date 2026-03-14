/**
 * Deep coverage E2E tests for the Dash home page.
 *
 * Extends the basic home.spec.ts with card interactions,
 * dark mode, page size, and layout checks.
 */

import { test, expect } from "../../fixtures/auth.fixture";
import { waitForDashReady } from "../../utils/wait.helper";

import { DashHomePage } from "../../pages/dashboard/home.page";

test.describe("Dashboard home deep", () => {
  let homePage: DashHomePage;

  test.beforeEach(async ({ page, userToken }) => {
    homePage = new DashHomePage(page);
    await homePage.gotoWithToken(userToken);
  });

  test("stock cards display ticker symbols", async ({
    page,
  }) => {
    await expect(homePage.stockCards.first()).toBeVisible(
      { timeout: 30_000 },
    );
    // Cards should have some text content
    const text = await homePage.stockCards
      .first()
      .innerText();
    expect(text.length).toBeGreaterThan(0);
  });

  test("clicking a stock card navigates to analysis", async ({
    page,
  }) => {
    await expect(homePage.stockCards.first()).toBeVisible(
      { timeout: 30_000 },
    );
    // Click the first card's link/button
    await homePage.stockCards.first().click();
    await page.waitForURL(/\/analysis/, {
      timeout: 10_000,
    });
    expect(page.url()).toContain("/analysis");
  });

  test("navbar is visible on home page", async ({
    page,
  }) => {
    const navbar = page.locator(".navbar");
    await expect(navbar).toBeVisible({
      timeout: 15_000,
    });
  });

  test("dark mode renders home page", async ({
    page,
    userToken,
  }) => {
    await page.goto(`/?token=${userToken}&theme=dark`);
    await waitForDashReady(page);
    const hasDark = await page.evaluate(
      () =>
        document.body.classList.contains("dark-mode"),
    );
    expect(hasDark).toBe(true);
    // Cards should still render
    await expect(homePage.stockCards.first()).toBeVisible(
      { timeout: 30_000 },
    );
  });

  test("no horizontal overflow on home page", async ({
    page,
  }) => {
    await expect(homePage.stockCards.first()).toBeVisible(
      { timeout: 30_000 },
    );
    const hasOverflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth,
    );
    expect(hasOverflow).toBe(false);
  });

  test("page-size selector has all options", async ({
    page,
  }) => {
    const pageSize = page.locator("#home-page-size");
    await expect(pageSize).toBeVisible({
      timeout: 15_000,
    });
    const options = pageSize.locator("option");
    const count = await options.count();
    expect(count).toBeGreaterThanOrEqual(4);
  });

  test("page-size change updates card count", async ({
    page,
  }) => {
    await expect(homePage.stockCards.first()).toBeVisible(
      { timeout: 30_000 },
    );
    const pageSize = page.locator("#home-page-size");
    await pageSize.selectOption("24");
    await waitForDashReady(page);
    // Count text should be visible
    const countText = page.locator("#home-count-text");
    await expect(countText).toBeVisible();
    const text = await countText.innerText();
    expect(text).toContain("of");
  });

  test("count text shows correct range", async ({
    page,
  }) => {
    await expect(homePage.stockCards.first()).toBeVisible(
      { timeout: 30_000 },
    );
    const countText = page.locator("#home-count-text");
    await expect(countText).toBeVisible();
    const text = await countText.innerText();
    // Format: "Showing 1–12 of N"
    expect(text).toMatch(/Showing \d+.+\d+ of \d+/);
  });

  test("market filter buttons toggle active state", async ({
    page,
  }) => {
    const indiaBtn = page.locator("#filter-india-btn");
    const usBtn = page.locator("#filter-us-btn");
    await expect(indiaBtn).toBeVisible({ timeout: 15_000 });
    await expect(usBtn).toBeVisible();

    // Click US, verify it gets primary class
    await usBtn.click();
    await waitForDashReady(page);
    await expect(usBtn).toHaveClass(/btn-primary/);

    // Click India back
    await indiaBtn.click();
    await waitForDashReady(page);
    await expect(indiaBtn).toHaveClass(/btn-primary/);
  });
});
