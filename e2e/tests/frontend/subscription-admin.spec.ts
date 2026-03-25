/**
 * E2E tests for admin subscription management.
 *
 * Tests the Maintenance tab (subscription cleanup,
 * usage reset) and Transactions tab.
 * Requires superuser login.
 */

import { test, expect } from "@playwright/test";

test.describe("Admin subscription management", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/admin");
    await expect(
      page.getByText("Users"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("maintenance tab loads", async ({
    page,
  }) => {
    await page
      .getByTestId("admin-tab-maintenance")
      .click();
    await expect(
      page.getByText("Triage Orphaned Subscriptions"),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByText("Reset Monthly Usage"),
    ).toBeVisible();
    await expect(
      page.getByText("Iceberg Data Retention"),
    ).toBeVisible();
    await expect(
      page.getByText("Query Gap Analysis"),
    ).toBeVisible();
  });

  test("subscription cleanup scan works", async ({
    page,
  }) => {
    await page
      .getByTestId("admin-tab-maintenance")
      .click();
    const scanBtn = page
      .getByRole("button", { name: "Scan" })
      .first();
    await scanBtn.click();

    // Should show triage table or "No active
    // subscriptions found"
    await expect(
      page.locator(
        "text=/Sub ID|No active subscriptions/",
      ),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("usage reset scan lists users", async ({
    page,
  }) => {
    await page
      .getByTestId("admin-tab-maintenance")
      .click();

    // Find the Scan button in the usage section
    const scanBtns = page.getByRole("button", {
      name: "Scan",
    });
    // Second Scan button is for usage reset
    if ((await scanBtns.count()) >= 2) {
      await scanBtns.nth(1).click();
      // Should show user table
      await expect(
        page.getByText("User").first(),
      ).toBeVisible({ timeout: 10_000 });
    }
  });

  test("transactions tab loads", async ({
    page,
  }) => {
    await page
      .getByTestId("admin-tab-transactions")
      .click();
    // Should show gateway filter and table
    // or "No transactions recorded"
    await expect(
      page.locator(
        "text=/All Gateways|No transactions/",
      ),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("transactions gateway filter works", async ({
    page,
  }) => {
    await page
      .getByTestId("admin-tab-transactions")
      .click();

    const select = page.locator("select").first();
    await select.selectOption("razorpay");
    // Should refresh (no crash)
    await page.waitForTimeout(1000);

    await select.selectOption("stripe");
    await page.waitForTimeout(1000);

    await select.selectOption("");
    // Back to all
  });
});
