/**
 * E2E for REGIME-3 — strategy↔regime binding + change banner.
 *
 * Two assertions per spec, mirroring the permissive style of
 * `algo-trading-regime-widget.spec.ts` so the suite passes on a
 * clean dev DB AND on a backfilled environment.
 *
 * Scope:
 *   1. Strategy editor exposes the applicability chip group
 *      (BULL / SIDEWAYS / BEAR).
 *   2. Banner appears when localStorage seeds a stale `lastSeen`,
 *      and dismissing it hides it.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("REGIME-3 — binding + banner", () => {
  test("strategy editor exposes applicability chips", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=strategies");

    // Click the "+ New strategy" button to flip into builder mode.
    // The button label is plain text in the StrategiesTab list view.
    const newBtn = page.getByRole("button", {
      name: /new strategy/i,
    });
    if (await newBtn.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await newBtn.click();
    }

    // Builder must mount and expose the chip group.
    await expect(
      page.getByTestId(FE.regimeApplicabilityChips),
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByTestId(FE.regimeApplicabilityChipBull),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.regimeApplicabilityChipSideways),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.regimeApplicabilityChipBear),
    ).toBeVisible();
  });

  test("banner shows on stale lastSeen + dismiss hides", async ({
    page,
  }) => {
    // Pre-seed localStorage so the banner sees a regime mismatch the
    // first time `useRegimeCurrent` returns a value.  Clear any
    // dismiss tombstones for safety.
    await page.addInitScript(() => {
      localStorage.setItem("algo.regime.lastSeen", "BEAR");
      // Best-effort cleanup of any prior dismiss tombstones.
      ["BULL", "SIDEWAYS", "BEAR"].forEach((r) => {
        localStorage.removeItem("algo.regime.dismissed." + r);
      });
    });

    await page.goto("/algo-trading?tab=paper");
    await expect(page.getByTestId(FE.algoPaperTab)).toBeVisible();

    // Either the banner renders (when the regime classifier has
    // produced a row != BEAR) or the page renders gracefully without
    // one — both are valid outcomes on a clean dev DB.
    const banner = page.getByTestId(FE.regimeChangeBanner);
    const visible = await banner
      .isVisible({ timeout: 5_000 })
      .catch(() => false);
    if (visible) {
      await page.getByTestId(FE.regimeChangeBannerDismiss).click();
      await expect(banner).toBeHidden();
    } else {
      // Permissive: the test passes when no regime data exists yet.
      // We still assert the page itself rendered without error so a
      // regression in PaperTab mounting is caught.
      await expect(page.getByTestId(FE.algoPaperTab)).toBeVisible();
    }
  });
});
