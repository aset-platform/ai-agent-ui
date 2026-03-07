/**
 * E2E tests for the Dash admin page (superuser only).
 */

import { test, expect } from "../../fixtures/auth.fixture";

import { DashAdminPage } from "../../pages/dashboard/admin.page";

test.describe("Dashboard admin", () => {
  let adminPage: DashAdminPage;

  test.beforeEach(async ({ page, adminToken }) => {
    adminPage = new DashAdminPage(page);
    await adminPage.gotoWithToken(adminToken);
  });

  test("admin page loads user table", async () => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
  });

  test("user table has at least 1 user", async () => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const rows = await adminPage.userRows.count();
    expect(rows).toBeGreaterThanOrEqual(1);
  });

  test("RBAC: general user sees forbidden", async ({
    page,
    userToken,
  }) => {
    // Navigate with a general user token (not admin)
    await page.goto(`/admin/users?token=${userToken}`);
    await page.waitForTimeout(3_000);
    // Should show access denied or redirect
    const forbidden = page.locator("text=access denied").or(
      page.locator("text=forbidden"),
    ).or(
      page.locator("text=not authorized"),
    ).or(
      page.locator("text=sign in"),
    );
    await expect(forbidden.first()).toBeVisible({
      timeout: 10_000,
    });
  });
});
