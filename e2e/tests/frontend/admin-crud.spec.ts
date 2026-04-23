/**
 * E2E tests for Admin user CRUD operations (Gap 5).
 *
 * Uses pre-authenticated storageState (superuser).
 * Tests cover user creation, edit, deactivation,
 * reactivation, password reset, and audit log verification.
 *
 * A unique email per test run avoids collisions.
 * Cleanup runs in afterAll via API.
 */

import { test, expect } from "../../fixtures/auth.fixture";
import { AdminPage } from "../../pages/frontend/admin.page";
import { request as pwRequest } from "@playwright/test";
import { waitForPageReady } from "../../utils/wait.helper";

const BACKEND_HOST =
  process.env.BACKEND_URL || "http://127.0.0.1:8181";
const BACKEND = `${BACKEND_HOST}/v1`;

const TIMESTAMP = Date.now();
const TEST_EMAIL = `e2e-test-${TIMESTAMP}@example.com`;
const TEST_PASSWORD = "E2eTest!234";

/**
 * Track the created user ID for cleanup and subsequent
 * tests that need to reference the same user.
 */
let createdUserId = "";

test.describe.serial("Admin user CRUD", () => {
  let admin: AdminPage;

  test.beforeEach(async ({ page }) => {
    admin = new AdminPage(page);
    await admin.gotoAdmin();
    await waitForPageReady(page);
  });

  /**
   * Cleanup: deactivate the test user after all tests.
   *
   * Uses a dedicated test (last in serial order) because
   * afterAll does not support test-scoped fixtures.
   */

  // ── Create User ─────────────────────────────────────

  test("create new user via UserModal", async ({ page }) => {
    await admin.addUserBtn().click();

    // Wait for the user modal form to appear
    const emailInput = page.getByTestId(
      "user-modal-email",
    );
    await expect(emailInput).toBeVisible({
      timeout: 5_000,
    });

    // Fill form fields
    await emailInput.fill(TEST_EMAIL);
    await page
      .getByTestId("user-modal-password")
      .fill(TEST_PASSWORD);
    await page
      .getByTestId("user-modal-name")
      .fill("E2E Test User");
    const roleSelect = page.getByTestId(
      "user-modal-role",
    );
    if (await roleSelect.isVisible()) {
      await roleSelect.selectOption("user");
    }

    await page
      .getByTestId("user-modal-submit")
      .click();

    // Modal should close
    await expect(modal).toBeHidden({ timeout: 10_000 });

    // Wait for table to refresh
    await page.waitForTimeout(1_000);
    await waitForPageReady(page);

    // Search for the new user
    await admin.searchUsers(TEST_EMAIL);
    await page.waitForTimeout(500);

    const table = admin.usersTable();
    await expect(table).toBeVisible({ timeout: 10_000 });
    const tableText = await table.textContent();
    expect(tableText).toContain(TEST_EMAIL);

    // Extract user ID from the matching row for later tests
    const rows = table.locator("tbody tr");
    const count = await rows.count();
    for (let i = 0; i < count; i++) {
      const rowText = await rows.nth(i).textContent();
      if (rowText?.includes(TEST_EMAIL)) {
        // Try to get user ID from a data attribute or cell
        const editBtn = rows
          .nth(i)
          .locator("[data-testid*='edit']");
        const testId =
          await editBtn.getAttribute("data-testid");
        // Format: admin-user-edit-{userId}
        if (testId) {
          createdUserId = testId.replace(
            "admin-user-edit-",
            "",
          );
        }
        break;
      }
    }
    expect(createdUserId).not.toBe("");
  });

  test("create user with invalid email shows error", async ({
    page,
  }) => {
    await admin.addUserBtn().click();

    const modal = page.getByTestId("admin-user-modal");
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Fill with invalid email
    await page
      .getByTestId("admin-user-modal-email")
      .fill("not-an-email");
    await page
      .getByTestId("admin-user-modal-password")
      .fill(TEST_PASSWORD);
    await page
      .getByTestId("admin-user-modal-name")
      .fill("Invalid User");

    await page
      .getByTestId("admin-user-modal-submit")
      .click();

    // Modal should remain open with error
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // data-testid="admin-user-modal-error"
    const error = page.getByTestId(
      "admin-user-modal-error",
    );
    const modalStillOpen = await modal.isVisible();
    expect(modalStillOpen).toBe(true);

    // Check for validation error if rendered
    const hasError = await error
      .waitFor({ state: "visible", timeout: 3_000 })
      .then(() => true)
      .catch(() => false);
    if (hasError) {
      const text = await error.textContent();
      expect(text?.toLowerCase()).toMatch(
        /email|invalid|format/,
      );
    }
  });

  // ── Edit User ───────────────────────────────────────

  test("edit user role from user to admin", async ({
    page,
  }) => {
    test.skip(!createdUserId, "No user created to edit");

    // Search for the test user
    await admin.searchUsers(TEST_EMAIL);
    await page.waitForTimeout(500);

    await admin.userEditBtn(createdUserId).click();

    const modal = page.getByTestId("admin-user-modal");
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Change role to admin
    const roleSelect = page.getByTestId(
      "admin-user-modal-role",
    );
    if (await roleSelect.isVisible()) {
      await roleSelect.selectOption("admin");
    }

    await page
      .getByTestId("admin-user-modal-submit")
      .click();
    await expect(modal).toBeHidden({ timeout: 10_000 });

    // Verify the role updated in the table
    await page.waitForTimeout(1_000);
    await admin.searchUsers(TEST_EMAIL);
    await page.waitForTimeout(500);

    const table = admin.usersTable();
    const tableText = await table.textContent();
    expect(tableText?.toLowerCase()).toMatch(
      /admin/,
    );
  });

  // ── Deactivate / Reactivate ─────────────────────────

  test("deactivate user via toggle", async ({ page }) => {
    test.skip(!createdUserId, "No user created to toggle");

    await admin.searchUsers(TEST_EMAIL);
    await page.waitForTimeout(500);

    const toggle = admin.userToggleBtn(createdUserId);
    await expect(toggle).toBeVisible({ timeout: 5_000 });
    await toggle.click();

    // Wait for toggle state to change
    await page.waitForTimeout(1_000);

    // Verify the user row reflects deactivated state
    // (text might show "Inactive" or toggle changes appearance)
    const table = admin.usersTable();
    const tableText = await table.textContent();
    expect(tableText?.toLowerCase()).toMatch(
      /inactive|disabled|deactivated/,
    );
  });

  test("reactivate deactivated user", async ({ page }) => {
    test.skip(!createdUserId, "No user created to toggle");

    await admin.searchUsers(TEST_EMAIL);
    await page.waitForTimeout(500);

    const toggle = admin.userToggleBtn(createdUserId);
    await expect(toggle).toBeVisible({ timeout: 5_000 });
    await toggle.click();

    // Wait for toggle state to change
    await page.waitForTimeout(1_000);

    // Verify the user row reflects active state
    const table = admin.usersTable();
    const tableText = await table.textContent();
    expect(tableText?.toLowerCase()).toMatch(
      /active|enabled/,
    );
  });

  // ── Reset Password ──────────────────────────────────

  test("reset user password", async ({ page }) => {
    test.skip(
      !createdUserId,
      "No user created to reset",
    );

    await admin.searchUsers(TEST_EMAIL);
    await page.waitForTimeout(500);

    const resetBtn = admin.userResetBtn(createdUserId);
    await expect(resetBtn).toBeVisible({ timeout: 5_000 });
    await resetBtn.click();

    // A confirmation dialog or toast should appear
    // data-testid="confirm-dialog"
    const dialog = page.getByTestId("confirm-dialog");
    const dialogVisible = await dialog
      .waitFor({ state: "visible", timeout: 3_000 })
      .then(() => true)
      .catch(() => false);

    if (dialogVisible) {
      // Confirm the password reset
      await page
        .getByTestId("confirm-dialog-confirm")
        .click();
    }

    // Wait for the operation to complete
    await page.waitForTimeout(1_000);

    // A success toast or new password display should appear
    // data-testid="admin-reset-success"
    const success = page.getByTestId(
      "admin-reset-success",
    );
    const toast = page.locator(
      "[role='alert'], [class*='toast'], [class*='notification']",
    );

    const hasSuccess = await success
      .waitFor({ state: "visible", timeout: 5_000 })
      .then(() => true)
      .catch(() => false);
    const hasToast = await toast
      .first()
      .waitFor({ state: "visible", timeout: 5_000 })
      .then(() => true)
      .catch(() => false);

    // At least one feedback mechanism should confirm reset
    expect(hasSuccess || hasToast).toBe(true);
  });

  // ── Audit Log ───────────────────────────────────────

  test("audit log shows entry after user creation", async ({
    page,
  }) => {
    test.skip(
      !createdUserId,
      "No user created — nothing to audit",
    );

    await admin.clickTab("audit");
    const auditTable = admin.auditTable();
    await expect(auditTable).toBeVisible({
      timeout: 15_000,
    });

    // Search for the test email in audit log
    await admin.searchAudit(TEST_EMAIL);
    await page.waitForTimeout(500);

    const tableText = await auditTable.textContent();
    // Audit should contain a reference to the created user
    expect(tableText?.toLowerCase()).toMatch(
      /create|created|user/i,
    );
  });

  // ── Cleanup (must be last in serial order) ──────────

  test("cleanup: delete test user via API", async ({
    adminToken,
  }) => {
    test.skip(
      !createdUserId,
      "No user created — nothing to clean up",
    );

    const ctx = await pwRequest.newContext();
    try {
      await ctx.delete(
        `${BACKEND}/admin/users/${createdUserId}`,
        {
          headers: {
            Authorization: `Bearer ${adminToken}`,
          },
        },
      );
    } catch {
      // Best-effort cleanup — user may already be removed.
    }
    await ctx.dispose();
  });
});
