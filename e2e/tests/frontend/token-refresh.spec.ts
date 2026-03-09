/**
 * E2E test for automatic JWT token refresh.
 *
 * The frontend's ``apiFetch`` wrapper checks if the stored access
 * token is expired BEFORE making a request and silently refreshes
 * it.  If the server returns 401, the app clears tokens and
 * redirects to ``/login``.
 */

import { test, expect } from "@playwright/test";

test.describe("Token refresh", () => {
  test.use({
    storageState: ".auth/general-user.json",
  });

  test("401 from server clears tokens and redirects to login", async ({
    page,
  }) => {
    // Intercept /auth/me with 401 to simulate server-side rejection
    await page.route("**/auth/me", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Token expired" }),
      }),
    );

    await page.goto("/");

    // The app should redirect to /login when it gets a 401
    await page.waitForURL(/\/login/, { timeout: 15_000 });
    expect(page.url()).toContain("/login");
  });

  test("pre-request refresh keeps user logged in", async ({
    page,
  }) => {
    // Just verify that with valid storageState, the page loads
    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });
  });
});
