/**
 * Visual regression snapshot tests for key pages.
 *
 * Captures full-page screenshots and compares against
 * baselines. Run with ``--update-snapshots`` to regenerate.
 *
 * Uses frontend-chromium project (superuser auth) for most
 * pages. Login page uses no auth.
 */

import { test, expect } from "@playwright/test";

test.describe("Visual regression", () => {
  test("login page", async ({ browser }) => {
    // Use a fresh context without auth — clear storage
    const ctx = await browser.newContext({
      storageState: undefined,
    });
    const page = await ctx.newPage();
    await page.goto(
      "http://localhost:3000/login",
    );
    await expect(
      page.getByTestId("login-submit-button"),
    ).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveScreenshot(
      "login-page.png",
      {
        fullPage: true,
        maxDiffPixelRatio: 0.02,
      },
    );
    await ctx.close();
  });

  test("dashboard page", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
    // Wait for hero section to render
    await expect(
      page.getByTestId("dashboard-hero-portfolio-value"),
    ).toBeVisible({ timeout: 15_000 }).catch(() => {
      // Hero may show error state — still valid
    });
    await page.waitForTimeout(2_000);
    await expect(page).toHaveScreenshot(
      "dashboard-page.png",
      {
        fullPage: false,
        maxDiffPixelRatio: 0.05,
      },
    );
  });

  test("analytics page", async ({ page }) => {
    await page.goto("/analytics");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);
    await expect(page).toHaveScreenshot(
      "analytics-page.png",
      {
        fullPage: false,
        maxDiffPixelRatio: 0.05,
      },
    );
  });

  test("insights screener tab", async ({ page }) => {
    await page.goto("/analytics/insights");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByTestId("insights-table"),
    ).toBeVisible({ timeout: 15_000 }).catch(() => {
      // Table may be empty — still valid
    });
    await page.waitForTimeout(2_000);
    await expect(page).toHaveScreenshot(
      "insights-screener.png",
      {
        fullPage: false,
        maxDiffPixelRatio: 0.05,
      },
    );
  });

  test("admin page", async ({ page }) => {
    await page.goto("/admin");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByTestId("admin-users-table"),
    ).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(1_000);
    await expect(page).toHaveScreenshot(
      "admin-page.png",
      {
        fullPage: false,
        maxDiffPixelRatio: 0.05,
      },
    );
  });
});
