/**
 * E2E tests for the email/password login flow.
 */

import { test, expect } from "@playwright/test";

import { LoginPage } from "../../pages/frontend/login.page";

const VALID_EMAIL =
  process.env.TEST_USER_EMAIL || "test@demo.com";
const VALID_PASSWORD =
  process.env.TEST_USER_PASSWORD || "Test1234!";

test.describe("Login page", () => {
  let loginPage: LoginPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    await loginPage.goto();
  });

  test("shows login form with email, password, and submit", async () => {
    await expect(loginPage.emailInput).toBeVisible();
    await expect(loginPage.passwordInput).toBeVisible();
    await expect(loginPage.submitBtn).toBeVisible();
  });

  test("valid credentials → redirect to chat", async ({ page }) => {
    test.slow(); // rate-limit retries may need extra time

    // Retry login up to 3 times in case of 429 rate limiting
    // from prior test runs within the 15-min window.
    for (let attempt = 0; attempt < 3; attempt++) {
      await loginPage.login(VALID_EMAIL, VALID_PASSWORD);
      try {
        await page.waitForURL("/", { timeout: 15_000 });
        break;
      } catch {
        // Likely 429 — wait for rate limit to ease, then retry
        if (attempt < 2) {
          await page.waitForTimeout(3_000);
          await loginPage.goto();
        }
      }
    }

    expect(page.url()).toContain("/");
    expect(page.url()).not.toContain("/login");
  });

  test("invalid credentials → error message", async () => {
    await loginPage.login("bad@example.com", "wrongpassword");
    await loginPage.expectError();
  });

  test("unauthenticated access to / → redirect to login", async ({
    page,
  }) => {
    // Clear any stored tokens
    await page.evaluate(() => localStorage.clear());
    await page.goto("/");
    await page.waitForURL(/\/login/, { timeout: 10_000 });
    expect(page.url()).toContain("/login");
  });

  test("Google OAuth button is visible", async () => {
    await expect(loginPage.googleOAuthBtn).toBeVisible();
  });

  test("Google OAuth button navigates to consent URL", async ({
    page,
  }) => {
    // Mock the authorize endpoint to avoid actual Google redirect
    await page.route("**/auth/oauth/google/authorize**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          state: "test-state",
          authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?test=1",
        }),
      }),
    );
    await loginPage.googleOAuthBtn.click();
    // Should attempt navigation to Google
    await page.waitForURL(/accounts\.google\.com|\/auth\/oauth/, {
      timeout: 10_000,
    });
  });
});
