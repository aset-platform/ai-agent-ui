/**
 * E2E tests for sidebar navigation and view switching.
 *
 * Desktop sidebar is always visible (md+). Navigation uses
 * Next.js <Link> routing — no iframes for dashboard/analytics.
 * Docs page still embeds MkDocs via iframe.
 */

import { test, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Navigation", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page.getByTestId(FE.sidebar),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("sidebar is visible with nav items", async ({
    page,
  }) => {
    // Portfolio (dashboard) and Docs should be visible
    await expect(
      page.getByTestId(FE.sidebarItem("dashboard")),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.sidebarItem("docs")),
    ).toBeVisible();
  });

  test("navigate to docs → iframe loads", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.sidebarItem("docs"))
      .click();
    await page.waitForURL("**/docs**");
    const iframe = page.locator("iframe");
    await expect(iframe).toBeVisible({
      timeout: 15_000,
    });
    const src = await iframe.getAttribute("src");
    expect(src).toContain("8000");
  });

  test("navigate to analytics → page loads", async ({
    page,
  }) => {
    // Sidebar defaults to collapsed — analytics group is
    // a direct link. Click it to navigate.
    await page
      .getByTestId(FE.sidebarGroup("analytics"))
      .click();
    await page.waitForURL("**/analytics**");
  });

  test("navigate back to portfolio from docs", async ({
    page,
  }) => {
    // Go to docs first
    await page
      .getByTestId(FE.sidebarItem("docs"))
      .click();
    await page.waitForURL("**/docs**");

    // Navigate back to portfolio
    await page
      .getByTestId(FE.sidebarItem("dashboard"))
      .click();
    await page.waitForURL("**/dashboard**");

    // Chat toggle should be available on dashboard
    await expect(
      page.getByTestId("chat-toggle"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("navigation preserves auth state", async ({
    page,
  }) => {
    // Navigate to docs
    await page
      .getByTestId(FE.sidebarItem("docs"))
      .click();
    await page.waitForURL("**/docs**");

    // Navigate back to dashboard
    await page
      .getByTestId(FE.sidebarItem("dashboard"))
      .click();
    await page.waitForURL("**/dashboard**");

    // Profile avatar should still be visible
    await expect(
      page.getByTestId(FE.profileAvatar),
    ).toBeVisible({ timeout: 15_000 });
  });
});
