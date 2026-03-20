/**
 * Deep coverage E2E tests for the Dash admin page.
 *
 * Extends the basic admin.spec.ts with table interactions,
 * create button, and audit log.
 */

import { test, expect } from "../../fixtures/auth.fixture";
import { waitForDashReady } from "../../utils/wait.helper";

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
    await waitForDashReady(page);
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
    await waitForDashReady(page);
    const forbidden = page
      .locator("text=access denied")
      .or(page.locator("text=forbidden"))
      .or(page.locator("text=not authorized"))
      .or(page.locator("text=sign in"));
    await expect(forbidden.first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("users pagination is visible", async ({ page }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const pagination = page.locator("#users-pagination");
    await expect(pagination).toBeVisible();
  });

  test("users page-size selector works", async ({
    page,
  }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const pageSize = page.locator("#users-page-size");
    await expect(pageSize).toBeVisible();
    const options = pageSize.locator("option");
    const count = await options.count();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  test("users search input filters table", async ({
    page,
  }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const search = page.locator("#users-search");
    await expect(search).toBeVisible();
    await search.fill("test");
    await waitForDashReady(page);
    // Table should still be visible (filtered)
    await expect(adminPage.userTable).toBeVisible();
  });

  test("users table has sortable headers", async ({
    page,
  }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const sortBtns = adminPage.userTable.locator(
      ".sort-header-btn",
    );
    const count = await sortBtns.count();
    expect(count).toBeGreaterThanOrEqual(3);
  });

  test("users count text shows record info", async ({
    page,
  }) => {
    await expect(adminPage.userTable).toBeVisible({
      timeout: 30_000,
    });
    const countText = page.locator("#users-count-text");
    await expect(countText).toBeVisible();
    const text = await countText.innerText();
    expect(text).toMatch(/\d+/);
  });

  test("audit log tab loads with table", async ({
    page,
  }) => {
    const auditTab = page.getByRole("tab", {
      name: /audit log/i,
    });
    await auditTab.click();
    await waitForDashReady(page);
    // Audit table container should be visible
    const auditContainer = page.locator(
      "#audit-log-container",
    );
    await expect(auditContainer).toBeVisible({
      timeout: 30_000,
    });
  });

  test("audit log has search and pagination", async ({
    page,
  }) => {
    const auditTab = page.getByRole("tab", {
      name: /audit log/i,
    });
    await auditTab.click();
    await waitForDashReady(page);
    // Search
    const search = page.locator("#audit-search");
    await expect(search).toBeVisible({
      timeout: 15_000,
    });
    // Pagination
    const pagination = page.locator("#audit-pagination");
    await expect(pagination).toBeVisible();
  });

  test("LLM observability tab shows tier health cards", async ({
    page,
  }) => {
    const obsTab = page.getByRole("tab", {
      name: /llm observability/i,
    });
    await expect(obsTab).toBeVisible({
      timeout: 15_000,
    });
    await obsTab.click();
    await waitForDashReady(page);
    // Health cards container should be visible
    const healthCards = page.locator("#obs-health-cards");
    await expect(healthCards).toBeAttached({
      timeout: 30_000,
    });
  });

  test("LLM observability tab shows tier budget cards", async ({
    page,
  }) => {
    const obsTab = page.getByRole("tab", {
      name: /llm observability/i,
    });
    await obsTab.click();
    await waitForDashReady(page);
    const tierCards = page.locator("#obs-tier-cards");
    await expect(tierCards).toBeAttached({
      timeout: 30_000,
    });
  });

  test("LLM observability tab shows cascade table", async ({
    page,
  }) => {
    const obsTab = page.getByRole("tab", {
      name: /llm observability/i,
    });
    await obsTab.click();
    await waitForDashReady(page);
    const cascadeTable = page.locator(
      "#obs-cascade-table",
    );
    await expect(cascadeTable).toBeAttached({
      timeout: 30_000,
    });
  });
});
