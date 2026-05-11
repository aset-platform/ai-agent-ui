/**
 * Strategies page — 8-tab strip (Slice 6 of the three-page split).
 *
 * Walks the entire tab strip in order, asserts the URL `?tab=`
 * stays in sync, and the corresponding panel testid is visible.
 * Also verifies the Dry-run tab shows the amber arm banner +
 * arm button — proves the page-toggle removal didn't drop the
 * arm UX.
 *
 * Auth: superuser storageState.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

const TABS = [
  "instruments",
  "strategies",
  "backtest",
  "paper",
  "dryrun",
  "performance",
  "replay",
  "settings",
] as const;

test.describe("Strategies tabs", () => {
  test("all 8 tabs reachable, URL syncs", async ({ page }) => {
    await page.goto("/algo-trading/strategies");
    await expect(
      page.getByTestId(FE.algoStrategiesHeading),
    ).toBeVisible();

    for (const id of TABS) {
      await page.getByTestId(FE.algoStrategiesTab(id)).click();
      await expect(page).toHaveURL(new RegExp(`tab=${id}`));
      await expect(
        page.getByTestId(FE.algoStrategiesPanel(id)),
      ).toBeVisible();
    }
  });

  test("Dry run tab shows amber arm banner", async ({ page }) => {
    await page.goto("/algo-trading/strategies?tab=dryrun");
    await expect(
      page.getByTestId(FE.algoDryRunBanner),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoDryRunArmBtn),
    ).toBeVisible();
  });
});
