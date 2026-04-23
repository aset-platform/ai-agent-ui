/**
 * E2E tests for the Insights Piotroski F-Score tab.
 *
 * Uses analytics-chromium project (general user auth).
 */

import { test, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Insights — Piotroski F-Score", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/analytics/insights");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("piotroski tab is visible and clickable", async ({
    page,
  }) => {
    const tab = page.getByTestId(
      FE.insightsTab("piotroski"),
    );
    await tab.scrollIntoViewIfNeeded();
    await expect(tab).toBeVisible({ timeout: 5_000 });
    await tab.click();
  });

  test("piotroski tab shows data table", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("piotroski"))
      .click();

    const table = page.getByTestId(FE.insightsTable);
    await expect(table).toBeVisible({
      timeout: 15_000,
    });
  });

  test("piotroski sector filter is visible", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("piotroski"))
      .click();

    const filter = page.getByTestId(
      "piotroski-sector-filter",
    );
    await expect(filter).toBeVisible({
      timeout: 10_000,
    });
  });

  test("piotroski market filter toggles", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("piotroski"))
      .click();

    const filter = page.getByTestId(
      "piotroski-market-filter",
    );
    await expect(filter).toBeVisible({
      timeout: 10_000,
    });
  });

  test("piotroski score filter is visible", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("piotroski"))
      .click();

    const filter = page.getByTestId(
      "piotroski-score-filter",
    );
    await expect(filter).toBeVisible({
      timeout: 10_000,
    });
  });

  test("piotroski table has score column", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("piotroski"))
      .click();

    const table = page.getByTestId(FE.insightsTable);
    await expect(table).toBeVisible({
      timeout: 15_000,
    });

    // Table should have a "Score" or "F-Score" header
    const header = table.locator(
      "th",
    ).filter({ hasText: /score/i });
    await expect(header.first()).toBeVisible();
  });
});
