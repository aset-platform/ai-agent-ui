/**
 * E2E smoke for the Live WS health dot — OBS-1.
 *
 * The PaperTab defaults to the Live segment, which renders the
 * Kite WS health dot (``data-testid="live-ws-health-dot"``)
 * inside the "Live order placement" section header. We only
 * assert the dot is present — colour semantics are covered by
 * vitest unit tests on ``statusFromAge``.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.describe("Algo Trading — Live WS health dot (OBS-1)", () => {
  test("dot renders inside the Live segment header", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=paper");
    await expect(page.getByTestId(FE.algoPaperTab)).toBeVisible();
    // Dot lives inside the Live segment, mounted by default.
    await expect(
      page.getByTestId(FE.algoLiveWsHealthDot),
    ).toBeVisible();
  });
});
