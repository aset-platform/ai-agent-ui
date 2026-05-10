/**
 * E2E — Kite Postback Panel (OBS-4).
 *
 * Three scenarios tested against the Live segment of the Trading tab.
 * The backend GET /algo/live/postbacks endpoint is mocked via
 * page.route so these tests don't require OBS-2 to be deployed.
 *
 * Auth: uses the superuser storageState (.auth/superuser.json) because
 * the endpoint requires pro_or_superuser and the Playwright auth setup
 * project already populates this file.
 */
import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

const POSTBACKS_URL = "**/v1/algo/live/postbacks*";

const FIXTURE_POSTBACK = {
  event_ts: "2026-05-10T09:30:00.000Z",
  tradingsymbol: "RELIANCE.NS",
  status: "COMPLETE",
  filled_quantity: 5,
  average_price: 2950.75,
  raw: {
    order_id: "240510000111111",
    guid: "e2e-test-guid",
    status: "COMPLETE",
    tradingsymbol: "RELIANCE.NS",
    filled_quantity: 5,
    average_price: 2950.75,
    checksum: "00deadbeef",
  },
};

test.describe("Algo Trading — Kite Postback Panel", () => {
  test.use({ storageState: ".auth/superuser.json" });

  test("panel is present in Live mode", async ({ page }) => {
    // Intercept the API so the test doesn't need OBS-2 deployed.
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");

    // Switch to Live mode using the trading-mode toggle.
    await page.getByTestId("trading-mode-live").click();

    // Select a strategy if dropdown is visible (panel only mounts
    // when liveStrategyId is set).
    const stratSelect = page.getByTestId("live-strategy-select");
    if (await stratSelect.isVisible()) {
      await stratSelect.selectOption({ index: 1 });
    }

    // The panel should be mounted (even with empty postbacks it
    // renders the panel container + empty state).
    await expect(
      page.getByTestId(FE.kitePostbackPanel),
    ).toBeVisible();
  });

  test("empty state shows troubleshooting card when postbacks is []", async ({
    page,
  }) => {
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");
    await page.getByTestId("trading-mode-live").click();

    const stratSelect = page.getByTestId("live-strategy-select");
    if (await stratSelect.isVisible()) {
      await stratSelect.selectOption({ index: 1 });
    }

    const emptyCard = page.getByTestId(FE.kitePostbackEmptyState);
    await expect(emptyCard).toBeVisible();
    await expect(emptyCard).toContainText("No postbacks received");
    await expect(emptyCard).toContainText("KITE_POSTBACK_ENABLED");
    await expect(emptyCard).toContainText("http://localhost:4040");
  });

  test("payload toggle expands raw JSON for a seeded postback row", async ({
    page,
  }) => {
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([FIXTURE_POSTBACK]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");
    await page.getByTestId("trading-mode-live").click();

    const stratSelect = page.getByTestId("live-strategy-select");
    if (await stratSelect.isVisible()) {
      await stratSelect.selectOption({ index: 1 });
    }

    // Wait for the postback row to appear.
    const row = page.getByTestId(FE.kitePostbackRow).first();
    await expect(row).toBeVisible();

    // Raw JSON is hidden before toggle.
    await expect(page.getByText("e2e-test-guid")).not.toBeVisible();

    // Click the arrow toggle.
    await page.getByTestId(FE.kitePostbackPayloadToggle).first().click();

    // Raw JSON should now be visible.
    await expect(page.getByText(/e2e-test-guid/)).toBeVisible();
  });

  test("panel is absent in Paper mode", async ({ page }) => {
    // No route intercept needed — panel shouldn't mount at all.
    await page.goto("/algo-trading?tab=paper");

    // Switch to Paper mode.
    await page.getByTestId("trading-mode-paper").click();

    await expect(
      page.getByTestId(FE.kitePostbackPanel),
    ).not.toBeVisible();
  });

  test("panel is absent in Dry-run mode", async ({ page }) => {
    await page.route(POSTBACKS_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    await page.goto("/algo-trading?tab=paper");
    await page.getByTestId("trading-mode-dryrun").click();

    await expect(
      page.getByTestId(FE.kitePostbackPanel),
    ).not.toBeVisible();
  });
});
