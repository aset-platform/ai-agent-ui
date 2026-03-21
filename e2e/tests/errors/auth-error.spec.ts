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
      localStorage.setItem(
        "auth_access_token",
        "expired.jwt.token",
      );
    });

    // Mock refresh endpoint to also fail
    await page.route("**/auth/refresh", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "Invalid refresh token",
        }),
      }),
    );

    await page.goto("/");
    // Should redirect to login since token is invalid
    await page.waitForURL(/\/login/, { timeout: 15_000 });
  });
});
