import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Dashboard Algo tab", () => {
  test("renders and accepts a click", async ({ page }) => {
    await page.goto("/dashboard");

    // Algo tab button is present for superuser.
    const tab = page.getByTestId(FE.dashboardWatchlistTabAlgo);
    await expect(tab).toBeVisible();
    await tab.click();

    // Either the positions table OR the empty-state CTA
    // renders — we don't assume the test env has algo
    // positions. The test passes as long as one of the
    // two is visible (i.e. the route + cookie + gate work).
    const table = page.getByTestId(
      FE.dashboardAlgoPositionsTable,
    );
    const empty = page.getByTestId(
      FE.dashboardAlgoPositionsEmpty,
    );
    await expect(table.or(empty)).toBeVisible();
  });
});
