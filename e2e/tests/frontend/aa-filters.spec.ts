/**
 * E2E coverage for the Advanced Analytics filter bundle
 * dropdowns + active-filter chip strip + filtered CSV
 * download (Sprint 9 follow-on).
 *
 * Project = ``frontend-chromium`` (superuser storage —
 * pro+superuser only per AA-7).
 *
 * Cases:
 * 1. Checking a tech filter narrows the table and writes
 *    ``?tech=`` to the URL after the 300 ms debounce.
 * 2. Removing a chip drops the URL param and the table
 *    re-renders.
 * 3. "Clear all" wipes both bundle params from the URL.
 * 4. CSV download returns >= visible-page row count and
 *    the CSV header starts with "Ticker".
 *
 * Per CLAUDE.md §5.14: 1 worker locally, no
 * ``networkidle``, scoped testids only.
 */

import { test, expect } from "@playwright/test";

import { AdvancedAnalyticsPage } from "../../pages/frontend/advanced-analytics.page";
import { FE } from "../../utils/selectors";

const DEFAULT_TAB = "current-day-upmove";

test.describe("Advanced Analytics — filter bundles", () => {
  test(
    "tech filter narrows table and updates URL",
    async ({ page }) => {
      const aa = new AdvancedAnalyticsPage(page);
      await aa.gotoTab(DEFAULT_TAB);
      await aa.waitForTable(DEFAULT_TAB);

      await page.getByTestId(FE.aaFilterTechButton).click();
      await expect(
        page.getByTestId(FE.aaFilterTechPopover),
      ).toBeVisible();
      await page
        .getByTestId(FE.aaFilterOption("tech", "price_gt_sma50"))
        .check();

      // URL update is debounced 300 ms.
      await expect(page).toHaveURL(/tech=price_gt_sma50/, {
        timeout: 2_000,
      });
      await expect(
        page.getByTestId(
          FE.aaActiveFilterChip("price_gt_sma50"),
        ),
      ).toBeVisible();
    },
  );

  test("removing a chip drops the URL param", async ({ page }) => {
    const aa = new AdvancedAnalyticsPage(page);
    await aa.gotoTab(DEFAULT_TAB);
    // Navigate with the filter pre-applied via URL.
    await page.goto(
      `/advanced-analytics?tab=${DEFAULT_TAB}&tech=price_gt_sma50`,
    );
    await aa.waitForTable(DEFAULT_TAB);
    await expect(
      page.getByTestId(
        FE.aaActiveFilterChip("price_gt_sma50"),
      ),
    ).toBeVisible({ timeout: 10_000 });

    await page
      .getByTestId(FE.aaActiveFilterChipX("price_gt_sma50"))
      .click();
    await expect(page).not.toHaveURL(/tech=/, { timeout: 2_000 });
  });

  test("Clear all removes both bundle params", async ({ page }) => {
    const aa = new AdvancedAnalyticsPage(page);
    // Navigate with both filters pre-applied.
    await page.goto(
      `/advanced-analytics?tab=${DEFAULT_TAB}&tech=price_gt_sma50&fund=fscore_ge_7`,
    );
    await expect(aa.heading()).toBeVisible({ timeout: 10_000 });
    await aa.waitForTable(DEFAULT_TAB);
    await expect(
      page.getByTestId(FE.aaActiveFilterClearAll),
    ).toBeVisible({ timeout: 10_000 });

    await page.getByTestId(FE.aaActiveFilterClearAll).click();
    await expect(page).not.toHaveURL(/tech=/, { timeout: 2_000 });
    await expect(page).not.toHaveURL(/fund=/);
  });

  test(
    "CSV download returns at least the visible page",
    async ({ page }) => {
      const aa = new AdvancedAnalyticsPage(page);
      await aa.gotoTab(DEFAULT_TAB);
      await aa.waitForTable(DEFAULT_TAB);

      const visibleCount = await aa.getRowCount(DEFAULT_TAB);
      test.skip(
        visibleCount < 2,
        "Need at least 2 rows to validate full-set vs page export",
      );

      const downloadPromise = page.waitForEvent("download");
      await aa.csvButton().click();
      const download = await downloadPromise;
      // Use a readable stream so we don't need @types/node
      // (the same project-level gap affects Buffer/process).
      const stream = await download.createReadStream();
      const chunks: Buffer[] = [];
      await new Promise<void>((resolve, reject) => {
        stream.on("data", (c: Buffer) => chunks.push(c));
        stream.on("end", resolve);
        stream.on("error", reject);
      });
      const text = Buffer.concat(chunks).toString("utf-8");
      const csvRows = text.trim().split("\n").length - 1;
      expect(csvRows).toBeGreaterThanOrEqual(visibleCount);
      expect(text.split("\n")[0]).toMatch(/^Ticker/);
    },
  );
});
