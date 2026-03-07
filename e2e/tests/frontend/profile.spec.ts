/**
 * E2E tests for profile editing and password change.
 */

import { test, expect } from "@playwright/test";

import { ProfileModalPage } from "../../pages/frontend/profile-modal.page";

test.describe("Profile management", () => {
  let profilePage: ProfileModalPage;

  test.beforeEach(async ({ page }) => {
    profilePage = new ProfileModalPage(page);
    await page.goto("/");
    await expect(
      page.getByTestId("chat-message-input"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("open edit profile modal", async () => {
    await profilePage.openEditProfile();
    await expect(profilePage.editProfileModal).toBeVisible();
  });

  test("open change password modal", async () => {
    await profilePage.openChangePassword();
    await expect(
      profilePage.changePasswordModal,
    ).toBeVisible();
  });
});
