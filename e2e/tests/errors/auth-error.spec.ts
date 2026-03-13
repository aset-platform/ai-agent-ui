/**
 * E2E tests for authentication error handling.
 */

import { test, expect } from "@playwright/test";

test.describe("Auth error handling", () => {
  test("expired JWT on frontend → redirect to login", async ({
    page,
  }) => {
    // Set an expired/invalid token in localStorage
    await page.goto("/login");
    await page.evaluate(() => {
      localStorage.setItem("auth_access_token", "expired.jwt.token");
    });

    // Mock refresh endpoint to also fail
    await page.route("**/auth/refresh", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Invalid refresh token" }),
      }),
    );

    await page.goto("/");
    // Should redirect to login since token is invalid
    await page.waitForURL(/\/login/, { timeout: 15_000 });
  });

  test("expired JWT on dashboard → unauth notice", async ({
    page,
  }) => {
    const DASHBOARD =
      process.env.DASHBOARD_URL || "http://127.0.0.1:8050";
    await page.goto(
      `${DASHBOARD}/?token=expired.jwt.token`,
    );
    await page.waitForTimeout(3_000);

    // Dash shows an unauthenticated notice
    const unauthNotice = page
      .locator("text=sign in")
      .or(page.locator("text=authenticate"))
      .or(page.locator("text=log in"));
    await expect(unauthNotice.first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("dashboard sign-in button redirects to login", async ({
    page,
  }) => {
    const DASHBOARD =
      process.env.DASHBOARD_URL || "http://127.0.0.1:8050";
    await page.goto(
      `${DASHBOARD}/?token=expired.jwt.token`,
    );
    await page.waitForTimeout(3_000);

    // Click the "Sign in" button on the auth overlay
    const signInBtn = page
      .locator("a, button")
      .filter({ hasText: /sign in/i })
      .first();
    await expect(signInBtn).toBeVisible({ timeout: 10_000 });
    await signInBtn.click();

    // Should navigate to the login page
    await page.waitForURL(/\/login/, { timeout: 15_000 });
  });
});
