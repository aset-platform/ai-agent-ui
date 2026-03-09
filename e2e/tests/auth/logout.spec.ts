/**
 * E2E tests for the logout flow.
 */

import { test, expect } from "@playwright/test";

import { LoginPage } from "../../pages/frontend/login.page";
import { ProfileModalPage } from "../../pages/frontend/profile-modal.page";

const EMAIL =
  process.env.TEST_USER_EMAIL || "test@example.com";
const PASSWORD =
  process.env.TEST_USER_PASSWORD || "TestPassword123!";

test.describe("Logout", () => {
  test("sign out clears tokens and redirects to login", async ({
    page,
  }) => {
    // Log in first
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.loginAndWaitForChat(EMAIL, PASSWORD);

    // Now sign out
    const profilePage = new ProfileModalPage(page);
    await profilePage.signOut();

    // Should redirect to login
    await page.waitForURL(/\/login/, { timeout: 10_000 });

    // Tokens should be cleared
    const token = await page.evaluate(() =>
      localStorage.getItem("auth_access_token"),
    );
    expect(token).toBeNull();
  });
});
