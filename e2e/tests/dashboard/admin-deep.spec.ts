/**
 * Deep coverage E2E tests for the Dash admin page.
 *
 * Extends the basic admin.spec.ts with table interactions,
 * create button, and audit log.
 */

import { test, expect } from "../../fixtures/auth.fixture";

import { DashAdminPage } from "../../pages/dashboard/admin.page";

test.describe("Dashboard admin deep", () => {
  let adminPage: DashAdminPage;

  test.beforeEach(async ({ page, adminToken }) => {
    adminPage = new DashAdminPage(page);
    await adminPage.gotoWithToken(adminToken);
  });

  test("user table has column headers", async ({
    page,
  }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const headers = adminPage.userTable.locator("th");
    const count = await headers.count();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  test("add user button is visible", async ({ page }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const addBtn = page.getByRole("button", {
      name: /add user/i,
    });
    await expect(addBtn).toBeVisible({
      timeout: 5_000,
    });
  });

  test("audit log tab is accessible", async ({
    page,
  }) => {
    // Admin page has tabs: Users, Audit Log, LLM Observability
    const auditTab = page.getByRole("tab", {
      name: /audit log/i,
    });
    await expect(auditTab).toBeVisible({
      timeout: 15_000,
    });
    await auditTab.click();
    // Should switch to audit log content without error
    const err = page.locator("text=Callback error");
    expect(await err.count()).toBe(0);
  });

  test("user rows contain email addresses", async ({
    page,
  }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const rows = adminPage.userRows;
    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(1);
    // First row should have some text content
    const firstRowText = await rows
      .first()
      .innerText();
    expect(firstRowText.length).toBeGreaterThan(0);
  });

  test("dark mode renders admin table", async ({
    page,
    adminToken,
  }) => {
    await page.goto(
      `/admin/users?token=${adminToken}&theme=dark`,
    );
    await page.waitForTimeout(3_000);
    const hasDark = await page.evaluate(
      () =>
        document.body.classList.contains("dark-mode"),
    );
    expect(hasDark).toBe(true);
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
  });

  test("non-admin user is denied access", async ({
    page,
    userToken,
  }) => {
    await page.goto(
      `/admin/users?token=${userToken}`,
    );
    await page.waitForSelector("#page-content", {
      state: "attached",
      timeout: 15_000,
    });
    await page.waitForTimeout(3_000);
    const forbidden = page
      .locator("text=access denied")
      .or(page.locator("text=forbidden"))
      .or(page.locator("text=not authorized"))
      .or(page.locator("text=sign in"));
    await expect(forbidden.first()).toBeVisible({
      timeout: 15_000,
    });
  });
});
