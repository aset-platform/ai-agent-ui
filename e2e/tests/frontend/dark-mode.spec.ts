/**
 * E2E tests for dark mode theme toggling and persistence.
 *
 * Uses pre-authenticated storageState (general user).
 */

import { test, expect } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";

test.describe("Dark mode", () => {
  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
    await chatPage.goto();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });
  });

  test("theme toggle switches between light and dark", async ({
    page,
  }) => {
    // Open nav drawer
    const navBtn = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn.click();
    // Use the visible Dark Mode / Light Mode button
    const themeBtn = page.getByRole("button", {
      name: /dark mode|light mode/i,
    });
    await expect(themeBtn.first()).toBeVisible({
      timeout: 5_000,
    });

    // Get initial theme
    const initialTheme = await page.evaluate(
      () => localStorage.getItem("theme") || "light",
    );

    // Toggle theme
    await themeBtn.first().click();
    const newTheme = await page.evaluate(
      () => localStorage.getItem("theme"),
    );
    expect(newTheme).not.toBe(initialTheme);
  });

  test("dark mode persists across page reload", async ({
    page,
  }) => {
    // Set dark mode
    await page.evaluate(
      () => localStorage.setItem("theme", "dark"),
    );
    await page.reload();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });

    const theme = await page.evaluate(
      () => localStorage.getItem("theme"),
    );
    expect(theme).toBe("dark");
  });

  test("dark mode applies correct body class", async ({
    page,
  }) => {
    await page.evaluate(
      () => localStorage.setItem("theme", "dark"),
    );
    await page.reload();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });

    const hasDarkClass = await page.evaluate(
      () => document.documentElement.classList.contains("dark"),
    );
    expect(hasDarkClass).toBe(true);
  });

  test("switching view preserves dark mode", async ({ page }) => {
    // Set dark mode
    await page.evaluate(
      () => localStorage.setItem("theme", "dark"),
    );
    await page.reload();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });

    // Switch to dashboard view via nav drawer
    const navBtn = page.getByRole("button", {
      name: /open navigation/i,
    });
    await navBtn.click();
    const dashBtn = page.getByRole("button", {
      name: /dashboard/i,
    });
    await dashBtn.first().click();
    await page.waitForTimeout(2_000);

    // Theme should still be dark
    const theme = await page.evaluate(
      () => localStorage.getItem("theme"),
    );
    expect(theme).toBe("dark");
  });
});
