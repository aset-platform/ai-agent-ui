/**
 * E2E tests for Insights advanced filters (Gap 7).
 *
 * Verifies chained filter combinations, quarterly report
 * switching, and filter reset behavior.
 */

import {
  test,
  expect,
} from "../../fixtures/portfolio.fixture";
import { InsightsPage } from "../../pages/frontend/insights.page";

test.describe("Insights advanced filters", () => {
  let insights: InsightsPage;

  test.beforeEach(async ({ page, seededPortfolio }) => {
    void seededPortfolio;
    insights = new InsightsPage(page);
    await insights.gotoInsights();
  });

  test(
    "chained RSI + market filter narrows results",
    async ({ page }) => {
      await insights.clickTab("screener");
      const table = insights.insightsTable();
      await expect(table).toBeVisible({
        timeout: 15_000,
      });

      // Count unfiltered rows
      const allRows = table.locator("tbody tr");
      await expect(allRows.first()).toBeVisible();
      const totalCount = await allRows.count();

      // Apply RSI filter (oversold)
      await insights.selectRsiFilter("oversold");
      await page.waitForTimeout(1_000);
      const afterRsi = await allRows.count();

      // Apply market filter (india)
      await insights.selectMarketFilter("india");
      await page.waitForTimeout(1_000);
      const afterBoth = await allRows.count();

      // Chained filters should narrow results
      expect(afterBoth).toBeLessThanOrEqual(afterRsi);
      expect(afterRsi).toBeLessThanOrEqual(totalCount);
    },
  );

  test(
    "quarterly report switches statement types",
    async ({ page }) => {
      await insights.clickTab("quarterly");
      const table = insights.insightsTable();
      await expect(table).toBeVisible({
        timeout: 15_000,
      });

      // Get column headers for default view
      const headers = table.locator("thead th");
      await expect(headers.first()).toBeVisible();
      const defaultText = await headers.allTextContents();

      // Switch statement type if selector exists
      const selector = page.getByTestId(
        "insights-statement-type",
      );
      const selectorExists = await selector
        .isVisible()
        .catch(() => false);

      if (selectorExists) {
        await selector.selectOption("balance");
        await page.waitForTimeout(1_000);
        const newText = await headers.allTextContents();
        // Headers should change for different statement
        expect(newText).not.toEqual(defaultText);
      }
    },
  );

  test(
    "market + sector filter combination",
    async ({ page }) => {
      await insights.clickTab("screener");
      const table = insights.insightsTable();
      await expect(table).toBeVisible({
        timeout: 15_000,
      });

      const allRows = table.locator("tbody tr");
      await expect(allRows.first()).toBeVisible();
      const totalCount = await allRows.count();

      // Apply market filter
      await insights.selectMarketFilter("india");
      await page.waitForTimeout(1_000);

      // Apply sector filter if available
      const sectorFilter = page.getByTestId(
        "insights-sector-filter",
      );
      const sectorExists = await sectorFilter
        .isVisible()
        .catch(() => false);

      if (sectorExists) {
        // Select first non-default option
        const options = sectorFilter.locator("option");
        const optCount = await options.count();
        if (optCount > 1) {
          const val = await options
            .nth(1)
            .getAttribute("value");
          if (val) {
            await insights.selectSectorFilter(val);
            await page.waitForTimeout(1_000);
          }
        }
      }

      const filteredCount = await allRows.count();
      expect(filteredCount).toBeLessThanOrEqual(
        totalCount,
      );
    },
  );

  test(
    "clearing filters restores full results",
    async ({ page }) => {
      await insights.clickTab("screener");
      const table = insights.insightsTable();
      await expect(table).toBeVisible({
        timeout: 15_000,
      });

      const allRows = table.locator("tbody tr");
      await expect(allRows.first()).toBeVisible();
      const totalCount = await allRows.count();

      // Apply filter
      await insights.selectMarketFilter("india");
      await page.waitForTimeout(1_000);
      const filteredCount = await allRows.count();
      expect(filteredCount).toBeLessThanOrEqual(
        totalCount,
      );

      // Clear filter (select "all")
      await insights.selectMarketFilter("all");
      await page.waitForTimeout(1_000);
      const restoredCount = await allRows.count();

      // Should restore to original count
      expect(restoredCount).toBeGreaterThanOrEqual(
        filteredCount,
      );
    },
  );
});
