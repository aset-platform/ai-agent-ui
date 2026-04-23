/**
 * E2E tests for the Insights Recommendations tab.
 *
 * Uses analytics-chromium project (general user auth).
 */

import { test, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Insights — Recommendations", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/analytics/insights");
    await expect(
      page.getByTestId("sidebar"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("recommendations tab is visible and clickable", async ({
    page,
  }) => {
    const tab = page.getByTestId(
      FE.insightsTab("recommendations"),
    );
    await tab.scrollIntoViewIfNeeded();
    await expect(tab).toBeVisible({ timeout: 5_000 });
    await tab.click();
  });

  test("recommendations tab renders without error", async ({
    page,
  }) => {
    await page
      .getByTestId(FE.insightsTab("recommendations"))
      .click();

    // Wait for tab content to load
    await page.waitForTimeout(3_000);

    // Verify the tab switched (no crash, content area
    // is not empty). The tab renders a custom component,
    // not the shared insights-table.
    const tabArea = page.locator(
      ".space-y-6 > div:last-child",
    );
    const text = await tabArea.textContent();
    expect(text?.length).toBeGreaterThan(0);
  });
});
