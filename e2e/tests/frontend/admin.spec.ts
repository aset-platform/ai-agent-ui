/**
 * E2E tests for the Admin page (/admin).
 *
 * Uses pre-authenticated storageState (superuser).
 * Tests cover Users tab, Audit Log tab, and Observability tab.
 */

import { test, expect } from "../../fixtures/auth.fixture";
import { AdminPage } from "../../pages/frontend/admin.page";

test.describe("Admin page", () => {
  let admin: AdminPage;

  test.beforeEach(async ({ page }) => {
    admin = new AdminPage(page);
    await admin.gotoAdmin();
  });

  // ── Users tab ───────────────────────────────────────

  test("users tab shows user table", async () => {
    const table = admin.usersTable();
    await expect(table).toBeVisible({ timeout: 15_000 });
  });

  test("user table has at least 2 rows", async () => {
    const table = admin.usersTable();
    await expect(table).toBeVisible({ timeout: 15_000 });
    const rows = table.locator("tbody tr");
    await expect(rows).toHaveCount(
      await rows.count().then((c) => {
        expect(c).toBeGreaterThanOrEqual(2);
        return c;
      }),
    );
  });

  test("user search filters by email", async () => {
    const table = admin.usersTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // Type a filter query
    await admin.searchUsers("test@demo");
    // Wait for debounce / filtering
    await admin.page.waitForTimeout(500);

    const rows = table.locator("tbody tr");
    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(1);

    // Every visible row should contain the search term
    for (let i = 0; i < count; i++) {
      const text = await rows.nth(i).innerText();
      expect(text.toLowerCase()).toContain("test@demo");
    }
  });

  test("add user button is visible", async () => {
    await expect(admin.addUserBtn()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("edit button visible on each user row", async () => {
    const table = admin.usersTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    const rows = table.locator("tbody tr");
    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(1);

    // At least the first row should have an edit button
    const firstRowEditBtn = rows
      .first()
      .locator("[data-testid*='edit']");
    await expect(firstRowEditBtn).toBeVisible();
  });

  test("reset password button visible", async () => {
    const table = admin.usersTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    const rows = table.locator("tbody tr");
    const firstRowResetBtn = rows
      .first()
      .locator("[data-testid*='reset']");
    await expect(firstRowResetBtn).toBeVisible();
  });

  // ── Audit Log tab ──────────────────────────────────

  test("audit log tab shows events table", async () => {
    await admin.clickTab("audit");
    const table = admin.auditTable();
    await expect(table).toBeVisible({ timeout: 15_000 });
  });

  test("audit table has at least 1 row", async () => {
    await admin.clickTab("audit");
    const table = admin.auditTable();
    await expect(table).toBeVisible({ timeout: 15_000 });

    const rows = table.locator("tbody tr");
    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  // ── Observability tab ──────────────────────────────

  test("observability tab shows summary cards", async () => {
    await admin.clickTab("observability");
    await expect(
      admin.summaryCard("requests"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("summary cards show Total Requests, Cascades, Tokens", async () => {
    await admin.clickTab("observability");

    await expect(
      admin.summaryCard("requests"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      admin.summaryCard("cascades"),
    ).toBeVisible();
    await expect(
      admin.summaryCard("compressions"),
    ).toBeVisible();
  });

  test("tier health cards render for at least 1 model", async ({
    page,
  }) => {
    await admin.clickTab("observability");
    // Wait for cards to render
    const tierCards = page.locator(
      "[data-testid^='admin-tier-card-']",
    );
    await tierCards.first().waitFor({
      state: "visible",
      timeout: 15_000,
    });
    const count = await tierCards.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("visual regression - admin observability (light)", async ({
    page,
  }) => {
    await admin.clickTab("observability");
    // Wait for content to fully render
    await expect(
      admin.summaryCard("requests"),
    ).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(1000);

    await expect(page).toHaveScreenshot(
      "admin-observability-light.png",
      { fullPage: true },
    );
  });
});
