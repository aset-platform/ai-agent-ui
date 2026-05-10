import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("REGIME-2b — Factor Scores tab", () => {
  test("tab strip exposes Factor Scores + page mounts", async ({
    page,
  }) => {
    await page.goto("/analytics/insights?tab=factors");
    // The tab button is `insights-tab-factors` per the existing
    // tab strip pattern in InsightsPageInner.
    await expect(
      page.getByTestId("insights-tab-factors"),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.factorScoresTab),
    ).toBeVisible();
    // The Columns selector is the always-rendered control;
    // verify it's visible (proves the tab structure is alive).
    await expect(
      page.getByRole("button", { name: /Columns/i }).first(),
    ).toBeVisible();
  });
});
