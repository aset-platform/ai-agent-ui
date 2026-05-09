/**
 * E2E smoke for the Algo Trading Performance + Replay tabs —
 * Slice 9 + 10. Verifies both tabs load and key controls render.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Algo Trading — Performance + Replay tabs", () => {
  test("performance tab loads with empty/aggregates state", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=performance");
    await expect(
      page.getByTestId(FE.algoPerformanceTab),
    ).toBeVisible();
  });

  test("replay tab loads with mode + type filters", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=replay");
    await expect(
      page.getByTestId(FE.algoReplayTab),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoReplayModeSelect),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoReplayTypeSelect),
    ).toBeVisible();
  });
});
