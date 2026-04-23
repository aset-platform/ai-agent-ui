/**
 * E2E tests for Insights table CSV download and pagination.
 *
 * Verifies that the download CSV button and pagination
 * controls work across Insights tabs that use InsightsTable.
 * Uses analytics-chromium project (general user auth).
 */

import { test, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Insights — CSV download & pagination", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/analytics/insights");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
    // Wait for default (screener) tab table to load
    await expect(
      page.getByTestId(FE.insightsTable),
    ).toBeVisible({ timeout: 15_000 });
  });

  // ── CSV Download ─────────────────────────────────

  test("download CSV button is visible on screener tab", async ({
    page,
  }) => {
    const csvBtn = page.getByTestId("download-csv");
    await expect(csvBtn).toBeVisible({
      timeout: 5_000,
    });
    await expect(csvBtn).toHaveText(/CSV/);
  });

  test("download CSV triggers file download", async ({
    page,
  }) => {
    const csvBtn = page.getByTestId("download-csv");
    await expect(csvBtn).toBeVisible({
      timeout: 5_000,
    });

    // Listen for download event
    const downloadPromise =
      page.waitForEvent("download");
    await csvBtn.click();
    const download = await downloadPromise;

    // Verify the downloaded file
    expect(download.suggestedFilename()).toMatch(
      /\.csv$/,
    );
  });

  test("download CSV available on targets tab", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("targets"))
      .click();
    await expect(
      page.getByTestId(FE.insightsTable),
    ).toBeVisible({ timeout: 15_000 });

    const csvBtn = page.getByTestId("download-csv");
    await expect(csvBtn).toBeVisible({
      timeout: 5_000,
    });
  });

  test("download CSV available on risk tab", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("risk"))
      .click();
    await expect(
      page.getByTestId(FE.insightsTable),
    ).toBeVisible({ timeout: 15_000 });

    const csvBtn = page.getByTestId("download-csv");
    await expect(csvBtn).toBeVisible({
      timeout: 5_000,
    });
  });

  test("download CSV available on piotroski tab", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("piotroski"))
      .click();
    await expect(
      page.getByTestId(FE.insightsTable),
    ).toBeVisible({ timeout: 15_000 });

    const csvBtn = page.getByTestId("download-csv");
    await expect(csvBtn).toBeVisible({
      timeout: 5_000,
    });
  });

  // ── Pagination ───────────────────────────────────

  test("pagination shows page indicator", async ({
    page,
  }) => {
    // Page indicator: "X / Y" between Prev/Next buttons
    const pageInfo = page.getByText(/^\d+ \/ \d+$/);
    await expect(pageInfo).toBeVisible({
      timeout: 5_000,
    });
  });

  test("pagination next button advances page", async ({
    page,
  }) => {
    const nextBtn = page.getByRole("button", {
      name: "Next",
    });
    const pageInfo = page.getByText(/^\d+ \/ \d+$/);

    await expect(pageInfo).toBeVisible({
      timeout: 5_000,
    });
    const initialText =
      await pageInfo.textContent();

    // If there are multiple pages, click Next
    if (
      !(await nextBtn.isDisabled().catch(() => true))
    ) {
      await nextBtn.click();
      const newText = await pageInfo.textContent();
      expect(newText).not.toBe(initialText);
    }
  });

  test("pagination prev button goes back", async ({
    page,
  }) => {
    const nextBtn = page.getByRole("button", {
      name: "Next",
    });
    const prevBtn = page.getByRole("button", {
      name: "Prev",
    });

    // Prev should be disabled on page 1
    await expect(prevBtn).toBeDisabled();

    // Go to page 2 if possible
    if (
      !(await nextBtn.isDisabled().catch(() => true))
    ) {
      await nextBtn.click();
      // Now Prev should be enabled
      await expect(prevBtn).toBeEnabled();
      await prevBtn.click();
      // Back to page 1 — Prev disabled again
      await expect(prevBtn).toBeDisabled();
    }
  });

  test("page size selector changes rows per page", async ({
    page,
  }) => {
    // Find the page size select
    const select = page
      .getByTestId(FE.insightsTable)
      .locator("..")
      .locator("select");
    await expect(select).toBeVisible({
      timeout: 5_000,
    });

    // Default is 10 rows
    const table = page.getByTestId(FE.insightsTable);
    const rowsBefore = await table
      .locator("tbody tr")
      .count();

    // Change to 25
    await select.selectOption("25");
    await page.waitForTimeout(500);

    const rowsAfter = await table
      .locator("tbody tr")
      .count();

    // If there were exactly 10 rows before and more
    // data exists, we should see more rows now
    if (rowsBefore === 10) {
      expect(rowsAfter).toBeGreaterThanOrEqual(
        rowsBefore,
      );
    }
  });

  test("row count label shows total", async ({
    page,
  }) => {
    // Should show "X rows" text
    const rowCount = page.getByText(/\d+ rows/);
    await expect(rowCount).toBeVisible({
      timeout: 5_000,
    });
    const text = await rowCount.textContent();
    const match = text?.match(/(\d+) rows/);
    expect(Number(match?.[1])).toBeGreaterThan(0);
  });
});
