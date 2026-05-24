import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Watchlist bulk ops", () => {
  test(
    "overflow menu opens both modals",
    async ({ page }) => {
      await page.goto("/dashboard");
      // Land on the Watchlist tab (Portfolio is default).
      await page.getByRole(
        "button", { name: /watchlist/i },
      ).click();
      await page.getByTestId(
        FE.dashboardWatchlistOverflowButton,
      ).click();
      await expect(
        page.getByTestId(
          FE.dashboardWatchlistOverflowMenu,
        ),
      ).toBeVisible();
      // Bulk add modal mounts.
      await page.getByTestId(
        FE.dashboardWatchlistBulkAddItem,
      ).click();
      await expect(
        page.getByTestId(FE.bulkAddTickersModal),
      ).toBeVisible();
    },
  );
});
