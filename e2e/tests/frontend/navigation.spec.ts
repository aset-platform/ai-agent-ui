/**
 * E2E tests for the navigation menu and view switching.
 */

import { test, expect } from "@playwright/test";

import { NavigationPage } from "../../pages/frontend/navigation.page";

test.describe("Navigation", () => {
  let navPage: NavigationPage;

  test.beforeEach(async ({ page }) => {
    navPage = new NavigationPage(page);
    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("menu toggle opens navigation panel", async () => {
    await navPage.openMenu();
    // At minimum, the "dashboard" nav item should be visible
    await expect(navPage.navItem("dashboard")).toBeVisible();
  });

  test("navigate to dashboard → iframe loads", async ({
    page,
  }) => {
    await navPage.navigateTo("dashboard");
    // The dashboard is loaded in an iframe
    const iframe = page.locator("iframe");
    await expect(iframe).toBeVisible({ timeout: 15_000 });
    const src = await iframe.getAttribute("src");
    expect(src).toContain("8050");
  });

  test("navigate to docs → iframe loads", async ({ page }) => {
    await navPage.navigateTo("docs");
    const iframe = page.locator("iframe");
    await expect(iframe).toBeVisible({ timeout: 15_000 });
    const src = await iframe.getAttribute("src");
    expect(src).toContain("8000");
  });
});
