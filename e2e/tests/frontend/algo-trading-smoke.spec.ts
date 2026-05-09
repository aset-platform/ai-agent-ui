/**
 * E2E smoke for the Algo Trading route — Slice 0 of the
 * epic. Verifies the page loads for a superuser and that
 * the tab strip is interactive.
 *
 * General-user 403 is gated at the nav menu (testid hidden);
 * the route itself doesn't enforce a server-side guard until
 * Slice 2 lands the backend router. So this spec only covers
 * the superuser-positive path.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: "e2e/.auth/superuser.json" });

test.describe("Algo Trading — Slice 0 smoke", () => {
  test("page loads with heading, tab strip, default Settings tab", async ({
    page,
  }) => {
    await page.goto("/algo-trading");
    await expect(page.getByTestId(FE.algoTradingHeading)).toHaveText(
      "Algo Trading",
    );
    await expect(page.getByTestId(FE.algoTradingTabs)).toBeVisible();
    await expect(
      page.getByTestId(FE.algoTradingPanel("settings")),
    ).toBeVisible({ timeout: 5_000 });
  });

  test("clicking a tab updates URL and renders placeholder", async ({
    page,
  }) => {
    await page.goto("/algo-trading");
    await page.getByTestId(FE.algoTradingTab("connect")).click();
    await expect(page).toHaveURL(/\?tab=connect/, { timeout: 2_000 });
    await expect(
      page.getByTestId(FE.algoTradingPanel("connect")),
    ).toBeVisible();
  });

  test("nav menu shows Algo Trading entry for superuser", async ({
    page,
  }) => {
    await page.goto("/dashboard");
    await page.getByTestId("nav-menu-toggle").click();
    await expect(
      page.getByTestId("nav-item-algo-trading"),
    ).toBeVisible();
  });
});
