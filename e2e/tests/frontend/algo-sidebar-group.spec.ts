/**
 * Algo Trading sidebar group — three-page split (Slice 6).
 *
 * Verifies the parent group expands, each child link routes to
 * the expected URL, and the legacy `/algo-trading?tab=settings`
 * query still resolves (via redirectMap.ts) to the new Strategies
 * page rather than 404.
 *
 * Auth: superuser storageState — algo-trading is superuser-only.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Algo Trading sidebar group", () => {
  test("expands and lands on each child", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByTestId(FE.algoSidebarGroup).click();
    await expect(
      page.getByTestId(FE.algoBrokerLink),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoStrategiesLink),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoLiveLink),
    ).toBeVisible();

    await page.getByTestId(FE.algoBrokerLink).click();
    await expect(page).toHaveURL(/\/algo-trading\/broker/);
    await expect(page.getByTestId(FE.algoBrokerPage)).toBeVisible();

    await page.getByTestId(FE.algoStrategiesLink).click();
    await expect(page).toHaveURL(/\/algo-trading\/strategies/);
    await expect(
      page.getByTestId(FE.algoStrategiesHeading),
    ).toBeVisible();

    await page.getByTestId(FE.algoLiveLink).click();
    await expect(page).toHaveURL(/\/algo-trading\/live/);
    await expect(
      page.getByTestId(FE.algoLiveHeader),
    ).toBeVisible();
  });

  test("legacy /algo-trading?tab=settings redirects to strategies", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=settings");
    await expect(page).toHaveURL(
      /\/algo-trading\/strategies\?tab=settings/,
    );
  });
});
