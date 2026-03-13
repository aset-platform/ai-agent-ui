/**
 * E2E tests for the navigation menu and view switching.
 */

import { test, expect } from "@playwright/test";

test.describe("Navigation", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("nav button opens navigation drawer", async ({
    page,
  }) => {
    const navBtn = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn.click();
    // At minimum, "Dashboard" option should be visible
    const dashBtn = page.getByRole("button", {
      name: /dashboard/i,
    });
    await expect(dashBtn.first()).toBeVisible({
      timeout: 5_000,
    });
  });

  test("navigate to dashboard → iframe loads", async ({
    page,
  }) => {
    const navBtn = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn.click();
    const dashBtn = page.getByRole("button", {
      name: /dashboard/i,
    });
    await dashBtn.first().click();
    // The dashboard is loaded in an iframe
    const iframe = page.locator("iframe");
    await expect(iframe).toBeVisible({ timeout: 15_000 });
    const src = await iframe.getAttribute("src");
    expect(src).toContain("8050");
  });

  test("navigate to docs → iframe loads", async ({ page }) => {
    const navBtn = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn.click();
    const docsBtn = page.getByRole("button", {
      name: /docs/i,
    });
    await docsBtn.first().click();
    const iframe = page.locator("iframe");
    await expect(iframe).toBeVisible({ timeout: 15_000 });
    const src = await iframe.getAttribute("src");
    expect(src).toContain("8000");
  });

  test("chat nav item returns to chat view", async ({
    page,
  }) => {
    // Navigate away first
    const navBtn = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn.click();
    const dashBtn = page.getByRole("button", {
      name: /dashboard/i,
    });
    await dashBtn.first().click();
    await page.waitForTimeout(2_000);

    // Navigate back to chat
    const navBtn2 = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn2.click();
    const chatBtn = page.getByRole("button", {
      name: /^chat$/i,
    });
    await chatBtn.first().click();

    // Chat input should reappear
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("navigation preserves auth state", async ({
    page,
  }) => {
    // Navigate to dashboard
    const navBtn = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn.click();
    const dashBtn = page.getByRole("button", {
      name: /dashboard/i,
    });
    await dashBtn.first().click();
    await page.waitForTimeout(2_000);

    // Navigate back to chat
    const navBtn2 = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn2.click();
    const chatBtn = page.getByRole("button", {
      name: /^chat$/i,
    });
    await chatBtn.first().click();

    // Profile avatar should still be visible (still authed)
    const avatar = page.getByTestId("profile-avatar");
    await expect(avatar).toBeVisible({ timeout: 15_000 });
  });
});
