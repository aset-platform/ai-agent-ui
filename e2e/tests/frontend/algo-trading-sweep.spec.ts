/**
 * E2E smoke for the Parameter Sweep sub-tab — Task 9 of the
 * walk-forward parameter sweep epic.
 *
 * Test: form-renders smoke — sub-tab routes correctly and the
 *   SweepForm mounts. A full submit-to-results test would
 *   require 10-15 min of real backend compute or a non-trivial
 *   API mock surface (sweep run + walkforward run polling).
 */

import { expect, test } from "@playwright/test";

import { SweepPage } from "../../pages/frontend/sweep.page";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Parameter sweep", () => {
  test("renders sub-tab and form", async ({ page }) => {
    const sweep = new SweepPage(page);
    await sweep.open();
    await expect(
      page.getByTestId(FE.sweepForm),
    ).toBeVisible();
  });
});
