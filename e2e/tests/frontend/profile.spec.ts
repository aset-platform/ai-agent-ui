/**
 * E2E tests for profile editing and password change.
 */

import { test, expect } from "@playwright/test";

import { ProfileModalPage } from "../../pages/frontend/profile-modal.page";

test.describe("Profile management", () => {
  let profilePage: ProfileModalPage;

  test.beforeEach(async ({ page }) => {
    profilePage = new ProfileModalPage(page);
    await page.goto("/dashboard");
    await expect(
      page.getByRole("button", { name: "Toggle chat panel" }),
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

  test("save profile name without error", async ({ page }) => {
    await profilePage.openEditProfile();
    await profilePage.fillFullName("Admin User");
    await profilePage.clickSave();

    // Modal should close (no network error) — wait for it
    // to disappear within a reasonable timeout.
    await expect(profilePage.editProfileModal).toBeHidden({
      timeout: 10_000,
    });

    // Verify no error message was shown
    const errorText = page.locator(
      '[data-testid="edit-profile-modal"] .text-red-500',
    );
    await expect(errorText).toBeHidden();
  });
});
