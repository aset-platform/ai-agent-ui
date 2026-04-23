/**
 * E2E tests for session management modal.
 *
 * Uses pre-authenticated storageState (general user).
 */

import { test, expect, type Page } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";

/** Open profile dropdown and click Manage Sessions. */
async function openSessionModal(
  chatPage: ChatPage,
  page: Page,
): Promise<void> {
  await chatPage.profileAvatar.click();
  const manageBtn = page.getByRole("button", {
    name: /manage sessions/i,
  });
  // Retry if dropdown didn't open (React hydration timing)
  if (!(await manageBtn.isVisible().catch(() => false))) {
    await page.waitForTimeout(500);
    await chatPage.profileAvatar.click();
  }
  await expect(manageBtn).toBeVisible({ timeout: 5_000 });
  await manageBtn.click();
  // Wait for modal heading
  const heading = page.getByRole("heading", {
    name: /active sessions/i,
  });
  await expect(heading).toBeVisible({ timeout: 10_000 });
}

test.describe("Session management", () => {
  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
    await chatPage.goto();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });
  });

  test("session management modal opens from profile menu", async ({
    page,
  }) => {
    await openSessionModal(chatPage, page);
  });

  test("session list shows session cards", async ({
    page,
  }) => {
    await openSessionModal(chatPage, page);

    // Wait for sessions to load — look for "Revoke" buttons
    // or session cards with device/browser info
    const revokeButtons = page.getByRole("button", {
      name: /^revoke$/i,
    });
    await expect(revokeButtons.first()).toBeVisible({
      timeout: 10_000,
    });

    // At least one session card should be visible
    const sessionCount = await revokeButtons.count();
    expect(sessionCount).toBeGreaterThan(0);
  });

  test("revoke all sessions button is visible", async ({
    page,
  }) => {
    await openSessionModal(chatPage, page);

    const revokeAllBtn = page.getByRole("button", {
      name: /revoke all/i,
    });
    await expect(revokeAllBtn).toBeVisible({
      timeout: 5_000,
    });
  });

  test("modal closes on close button click", async ({
    page,
  }) => {
    await openSessionModal(chatPage, page);

    // Close the modal — use the bottom "Close" button
    const closeButtons = page.getByRole("button", {
      name: /^close$/i,
    });
    await closeButtons.last().click();

    const heading = page.getByRole("heading", {
      name: /active sessions/i,
    });
    await expect(heading).toBeHidden({ timeout: 5_000 });
  });
});
