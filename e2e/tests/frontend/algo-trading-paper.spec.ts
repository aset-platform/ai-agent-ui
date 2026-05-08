/**
 * E2E smoke for the Algo Trading Paper tab + Kill switch — Slice 8b.
 * Verifies the tab loads with the events timeline (empty state)
 * and that the kill-switch toggle renders on Settings.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Algo Trading — Paper tab + Kill switch", () => {
  test("paper tab loads with timeline area visible", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=paper");
    await expect(page.getByTestId(FE.algoPaperTab)).toBeVisible();
  });

  test("settings tab shows kill switch toggle", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=settings");
    await expect(
      page.getByTestId(FE.algoKillSwitchToggle),
    ).toBeVisible();
  });
});
