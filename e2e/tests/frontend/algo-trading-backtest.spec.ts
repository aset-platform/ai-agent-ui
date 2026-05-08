/**
 * E2E smoke for the Algo Trading Backtest tab — Slice 7b.
 * Verifies the tab loads with the run form for a superuser.
 *
 * Full happy-path (submit → poll → equity curve render) is left
 * to a follow-up spec once a fixture strategy is seeded; the
 * smoke confirms the route + form components mount.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Algo Trading — Backtest tab smoke", () => {
  test("loads with empty state, run form is visible", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=backtest");
    await expect(page.getByTestId(FE.algoBacktestTab)).toBeVisible();
    await expect(
      page.getByTestId(FE.algoBacktestRunForm),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoBacktestSubmit),
    ).toBeVisible();
  });
});
