/**
 * E2E tests for dark mode theme toggling and persistence.
 *
 * Uses pre-authenticated storageState (superuser).
 * Theme toggle lives in the desktop sidebar footer.
 */

import { test, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Dark mode", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page.getByTestId(FE.sidebar),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("theme toggle switches between light and dark", async ({
    page,
  }) => {
    const themeBtn = page.getByTestId(
      FE.sidebarThemeToggle,
    );
    await expect(themeBtn).toBeVisible({ timeout: 5_000 });

    // Get initial theme
    const initialTheme = await page.evaluate(
      () => localStorage.getItem("theme") || "light",
    );

    // Toggle theme
    await themeBtn.click();
    const newTheme = await page.evaluate(
      () => localStorage.getItem("theme"),
    );
    expect(newTheme).not.toBe(initialTheme);
  });

  test("dark mode persists across page reload", async ({
    page,
  }) => {
    await page.evaluate(
      () => localStorage.setItem("theme", "dark"),
    );
    await page.reload();
    await expect(
      page.getByTestId(FE.sidebar),
    ).toBeVisible({ timeout: 15_000 });

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
    await expect(
      page.getByTestId(FE.sidebar),
    ).toBeVisible({ timeout: 15_000 });

    const hasDarkClass = await page.evaluate(
      () =>
        document.documentElement.classList.contains("dark"),
    );
    expect(hasDarkClass).toBe(true);
  });

  test("switching view preserves dark mode", async ({
    page,
  }) => {
    await page.evaluate(
      () => localStorage.setItem("theme", "dark"),
    );
    await page.reload();
    await expect(
      page.getByTestId(FE.sidebar),
    ).toBeVisible({ timeout: 15_000 });

    // Navigate to docs via sidebar
    await page
      .getByTestId(FE.sidebarItem("docs"))
      .click();
    await page.waitForURL("**/docs**");

    // Theme should still be dark
    const theme = await page.evaluate(
      () => localStorage.getItem("theme"),
    );
    expect(theme).toBe("dark");
  });
});
