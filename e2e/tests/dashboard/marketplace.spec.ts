/**
 * E2E tests for the Dash marketplace page.
 */

import { test, expect } from "../../fixtures/auth.fixture";
import { waitForDashReady } from "../../utils/wait.helper";

import { DashMarketplacePage } from "../../pages/dashboard/marketplace.page";

test.describe("Dashboard marketplace", () => {
  let marketplacePage: DashMarketplacePage;

  test.beforeEach(async ({ page, userToken }) => {
    marketplacePage = new DashMarketplacePage(page);
    await marketplacePage.gotoWithToken(userToken);
  });

  test("marketplace loads with ticker grid", async () => {
    await expect(marketplacePage.grid).toBeVisible({
      timeout: 30_000,
    });
  });

  test("add/remove buttons are present", async () => {
    const addCount = await marketplacePage.addButtons.count();
    const removeCount =
      await marketplacePage.removeButtons.count();
    // Should have at least some buttons
    expect(addCount + removeCount).toBeGreaterThan(0);
  });

  test("pagination works", async ({ page }) => {
    // Click next page if pagination exists
    const nextBtn = page.locator(
      ".pagination .page-link",
    ).last();
    if ((await nextBtn.count()) > 0) {
      await nextBtn.click({ force: true });
      await waitForDashReady(page);
      // Grid should still be visible after page change
      await expect(marketplacePage.grid).toBeVisible();
    }
  });
});
