/**
 * Zerodha Connect page — three-page split (Slice 6).
 *
 * The broker page is a single-screen host for ConnectBrokerTab.
 * This spec only asserts the page shell + heading render — the
 * Kite OAuth handshake is exercised by algo-trading-smoke.spec.ts.
 *
 * Auth: superuser storageState.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Zerodha Connect page", () => {
  test("renders broker page heading", async ({ page }) => {
    await page.goto("/algo-trading/broker");
    await expect(page.getByTestId(FE.algoBrokerPage)).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /zerodha connect/i }),
    ).toBeVisible();
  });
});
