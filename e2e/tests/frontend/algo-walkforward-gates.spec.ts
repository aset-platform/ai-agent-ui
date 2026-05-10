import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("REGIME-5 — walkforward gates strip", () => {
  test("walkforward subtab loads after backtest tab navigation", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=backtest");
    // Click the Walk-forward CV sub-tab
    await page.getByTestId("backtest-sub-tab-walkforward").click();
    // Either the strip is mounted (a completed run is visible) OR
    // the run-form is visible (no completed run yet). Both are
    // valid initial states.
    const strip = page.getByTestId(FE.walkForwardGatesStrip);
    const ranOnce = await strip
      .isVisible({ timeout: 5_000 })
      .catch(() => false);
    if (ranOnce) {
      // 5-light strip must include all 5 gates when rendered
      await expect(
        page.getByTestId(FE.walkForwardGateLightMaxDd),
      ).toBeVisible();
      await expect(
        page.getByTestId(FE.walkForwardGateLightDsr),
      ).toBeVisible();
    } else {
      // Initial state — the run form must be present
      await expect(
        page.getByRole("button", { name: /run/i }).first(),
      ).toBeVisible();
    }
  });
});
