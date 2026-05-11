/**
 * Live Trading dashboard — three-page split (Slice 6).
 *
 * Asserts the header strip + 4-zone grid (Open Positions / Regime
 * & Stress / Active Strategy / Recent Fills) render above the
 * fold at 1280×800. Also exercises the typed-confirm gate on the
 * PANIC CLOSE button — confirm stays disabled until "PANIC" is
 * typed, then becomes enabled.
 *
 * Auth: superuser storageState.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Live Trading dashboard", () => {
  test("header + 4 zones render above the fold", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/algo-trading/live");

    await expect(page.getByTestId(FE.algoLiveHeader)).toBeVisible();
    await expect(
      page.getByTestId(FE.algoLiveDashboard),
    ).toBeVisible();
    await expect(
      page.getByTestId("open-positions-widget"),
    ).toBeVisible();
    await expect(
      page.getByTestId("live-zone-b-regime"),
    ).toBeVisible();
    await expect(
      page.getByTestId("live-zone-c-strategy"),
    ).toBeVisible();
    await expect(
      page.getByTestId("recent-fills-tape"),
    ).toBeVisible();
  });

  test("panic close gated behind PANIC text", async ({ page }) => {
    await page.goto("/algo-trading/live");
    await page.getByTestId(FE.algoPanicButton).click();
    await expect(
      page.getByTestId(FE.algoPanicConfirm),
    ).toBeDisabled();
    await page.getByTestId(FE.algoPanicInput).fill("PANIC");
    await expect(
      page.getByTestId(FE.algoPanicConfirm),
    ).toBeEnabled();
  });
});
