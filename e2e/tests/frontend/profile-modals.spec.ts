/**
 * E2E tests for profile and password modals.
 *
 * Uses pre-authenticated storageState (general user).
 */

import { test, expect, type Page } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";

/** Open the profile dropdown with retry if first click doesn't work. */
async function openProfileDropdown(
  chatPage: ChatPage,
  page: Page,
): Promise<void> {
  await chatPage.profileAvatar.click();
  const editProfile = page.getByRole("button", {
    name: /edit profile/i,
  });
  // Retry if dropdown didn't open (React hydration timing)
  if (!(await editProfile.isVisible().catch(() => false))) {
    await page.waitForTimeout(500);
    await chatPage.profileAvatar.click();
  }
  await expect(editProfile).toBeVisible({ timeout: 5_000 });
}

test.describe("Profile & password modals", () => {
  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
    await chatPage.goto();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });
  });

  test("profile avatar is visible", async () => {
    await expect(chatPage.profileAvatar).toBeVisible({
      timeout: 5_000,
    });
  });

  test("profile menu shows edit profile option", async ({
    page,
  }) => {
    await openProfileDropdown(chatPage, page);
  });

  test("edit profile modal opens and has form fields", async ({
    page,
  }) => {
    await openProfileDropdown(chatPage, page);
    await page
      .getByRole("button", { name: /edit profile/i })
      .click();

    const modal = page.getByTestId("edit-profile-modal");
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Should have at least a name input
    const nameInput = modal.locator(
      "input[type='text'], input[name*='name'], input[placeholder]",
    );
    await expect(nameInput.first()).toBeVisible({
      timeout: 5_000,
    });
  });

  test("edit profile modal closes on cancel", async ({
    page,
  }) => {
    await openProfileDropdown(chatPage, page);
    await page
      .getByRole("button", { name: /edit profile/i })
      .click();

    const modal = page.getByTestId("edit-profile-modal");
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Use the Cancel button (not the X close icon)
    const cancelBtn = modal.getByRole("button", {
      name: /^cancel$/i,
    });
    await cancelBtn.click();
    await expect(modal).toBeHidden({ timeout: 5_000 });
  });

  test("change password option opens modal", async ({
    page,
  }) => {
    await openProfileDropdown(chatPage, page);
    await page
      .getByRole("button", { name: /change password/i })
      .click();

    const modal = page.getByTestId(
      "change-password-modal",
    );
    await expect(modal).toBeVisible({ timeout: 5_000 });
  });

  test("change password modal has required fields", async ({
    page,
  }) => {
    await openProfileDropdown(chatPage, page);
    await page
      .getByRole("button", { name: /change password/i })
      .click();

    const modal = page.getByTestId(
      "change-password-modal",
    );
    await expect(modal).toBeVisible({ timeout: 5_000 });

    const passwordInputs = modal.locator(
      "input[type='password']",
    );
    const count = await passwordInputs.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test("change password modal closes on cancel", async ({
    page,
  }) => {
    await openProfileDropdown(chatPage, page);
    await page
      .getByRole("button", { name: /change password/i })
      .click();

    const modal = page.getByTestId(
      "change-password-modal",
    );
    await expect(modal).toBeVisible({ timeout: 5_000 });

    const cancelBtn = modal.getByRole("button", {
      name: /^cancel$/i,
    });
    await cancelBtn.click();
    await expect(modal).toBeHidden({ timeout: 5_000 });
  });

  test("sign out option is visible in profile menu", async ({
    page,
  }) => {
    await openProfileDropdown(chatPage, page);
    const signOut = page.getByRole("button", {
      name: /sign out/i,
    });
    await expect(signOut).toBeVisible({
      timeout: 5_000,
    });
  });
});
