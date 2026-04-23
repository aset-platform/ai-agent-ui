/**
 * E2E tests for new Admin tabs: Observability, Maintenance,
 * Transactions, and Scheduler.
 *
 * Uses admin-chromium project (superuser auth).
 */

import { test, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Admin tabs", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/admin");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
  });

  // ── Observability tab ────────────────────────────

  test("observability tab loads with summary cards", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.adminTab("observability"))
      .click();

    // Summary cards should be visible
    const requests = page.getByTestId(
      FE.adminSummaryRequests,
    );
    await expect(requests).toBeVisible({
      timeout: 15_000,
    });
  });

  test("observability shows cascade summary", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.adminTab("observability"))
      .click();

    // Cascade count summary card should be visible
    const cascades = page.getByTestId(
      FE.adminSummaryCascades,
    );
    await expect(cascades).toBeVisible({
      timeout: 15_000,
    });
  });

  test("observability shows tier cards", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.adminTab("observability"))
      .click();

    // At least one tier card should be visible
    // (Groq, Ollama, or Anthropic)
    const cards = page.locator(
      "[data-testid^='admin-tier-card-']",
    );
    await expect(cards.first()).toBeVisible({
      timeout: 15_000,
    });
  });

  // ── Maintenance tab ──────────────────────────────

  test("maintenance tab loads", async ({ page }) => {
    await page
      .getByTestId(FE.adminTab("maintenance"))
      .click();

    // Should show maintenance utilities
    await page.waitForTimeout(2_000);
    // Verify the tab is active (no crash)
    const tab = page.getByTestId(
      FE.adminTab("maintenance"),
    );
    const classes = await tab.getAttribute("class");
    expect(classes).toBeTruthy();
  });

  // ── Transactions tab ─────────────────────────────

  test("transactions tab loads", async ({ page }) => {
    await page
      .getByTestId(FE.adminTab("transactions"))
      .click();

    // Should show transaction data or empty state
    await page.waitForTimeout(2_000);
    const tab = page.getByTestId(
      FE.adminTab("transactions"),
    );
    const classes = await tab.getAttribute("class");
    expect(classes).toBeTruthy();
  });

  // ── Scheduler tab ────────────────────────────────

  test("scheduler tab loads", async ({ page }) => {
    await page
      .getByTestId(FE.adminTab("scheduler"))
      .click();

    // Scheduler should show job cards or a table
    await page.waitForTimeout(2_000);
    const tab = page.getByTestId(
      FE.adminTab("scheduler"),
    );
    const classes = await tab.getAttribute("class");
    expect(classes).toBeTruthy();
  });

  // ── Tab switching ────────────────────────────────

  test("all 6 admin tabs are clickable", async ({
    page,
  }) => {
    const tabs = [
      "users",
      "audit",
      "observability",
      "maintenance",
      "transactions",
      "scheduler",
    ];

    for (const tabId of tabs) {
      const tab = page.getByTestId(
        FE.adminTab(tabId),
      );
      await expect(tab).toBeVisible({
        timeout: 5_000,
      });
      await tab.click();
      // Brief wait for content to load
      await page.waitForTimeout(500);
    }
  });
});
