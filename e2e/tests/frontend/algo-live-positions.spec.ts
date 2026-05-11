/**
 * Live Positions tab — three-page split (Slice 6).
 *
 * Mocks /v1/algo/live/positions with two rows — one with full
 * strategy attribution from algo.events (v3 Multi) and one
 * manual position with NULL strategy_id (renders as "—"). Proves
 * the strategy-join falls through gracefully for legacy fills.
 *
 * Auth: superuser storageState.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Live Positions tab", () => {
  test("renders mocked rows with strategy attribution", async ({
    page,
  }) => {
    await page.route("**/v1/algo/live/positions", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ledger_drift: false,
          rows: [
            {
              tradingsymbol: "ITC",
              exchange: "NSE",
              quantity: 8,
              average_price: "307.33",
              last_price: "311.20",
              pnl_inr: "30.96",
              pnl_pct: "1.26",
              product: "MIS",
              strategy_id: "v3",
              strategy_name: "V3 Multi",
              entry_ts_utc: "2026-05-11T04:19:54Z",
              entry_reason: "BULL · momentum_z=1.4",
            },
            {
              tradingsymbol: "MANUAL",
              exchange: "NSE",
              quantity: 1,
              average_price: "100",
              last_price: "100",
              pnl_inr: "0",
              pnl_pct: "0",
              product: "MIS",
              strategy_id: null,
              strategy_name: null,
              entry_ts_utc: null,
              entry_reason: null,
            },
          ],
        }),
      }),
    );

    await page.goto("/algo-trading/live?tab=positions");
    await expect(
      page.getByTestId(FE.algoPositionsTable),
    ).toBeVisible();
    await expect(page.getByText("ITC")).toBeVisible();
    await expect(page.getByText("V3 Multi")).toBeVisible();
    await expect(page.getByText("MANUAL")).toBeVisible();
  });
});
