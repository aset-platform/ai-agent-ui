/**
 * Deep coverage E2E tests for the Dash home page.
 *
 * Extends the basic home.spec.ts with card interactions,
 * dark mode, page size, and layout checks.
 */

import { test, expect } from "../../fixtures/auth.fixture";

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
    await page.waitForTimeout(3_000);
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
});
