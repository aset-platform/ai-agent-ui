/**
 * E2E coverage for the new Swing Setups tab under
 * Advanced Analytics.
 *
 * Project = ``frontend-chromium`` (superuser storage
 * state — pro+superuser only).
 *
 * Cases:
 * 1. Tab loads with regime pills + methodology panel.
 * 2. Switching regime pills swaps the methodology copy.
 * 3. Methodology panel can be collapsed via toggle and
 *    persists across page reloads (localStorage flag).
 *
 * Per CLAUDE.md §5.14: 1 worker locally, no
 * ``networkidle``, locator-scoped data-testids only.
 */

import { test, expect } from "@playwright/test";

import { AdvancedAnalyticsPage } from "../../pages/frontend/advanced-analytics.page";

test.describe("Advanced Analytics — Swing Setups", () => {
  test("tab loads with regime pills + methodology panel", async ({
    page,
  }) => {
    const aa = new AdvancedAnalyticsPage(page);
    await aa.gotoSwingSetups();

    // Active tab in the AA strip.
    await expect(page).toHaveURL(
      /\/advanced-analytics\?tab=swing-setups/,
    );

    // Three pills present.
    await expect(aa.swingPill("bull")).toBeVisible();
    await expect(aa.swingPill("sideways")).toBeVisible();
    await expect(aa.swingPill("bearish")).toBeVisible();

    // Default regime is bull — pill should be aria-selected.
    await expect(aa.swingPill("bull")).toHaveAttribute(
      "aria-selected", "true",
    );

    // Methodology panel renders (data drives the gate count
    // > 0; assert presence rather than exact count).
    await expect(aa.swingMethodologyPanel()).toBeVisible({
      timeout: 10_000,
    });
    await expect(aa.swingMethodologyPanel()).toContainText(
      /Bull-swing/i,
    );
  });

  test("switching regime swaps the methodology copy", async ({
    page,
  }) => {
    const aa = new AdvancedAnalyticsPage(page);
    await aa.gotoSwingSetups();
    await expect(aa.swingMethodologyPanel()).toBeVisible({
      timeout: 10_000,
    });

    await aa.swingPill("bearish").click();
    await expect(aa.swingPill("bearish")).toHaveAttribute(
      "aria-selected", "true",
    );
    await expect(aa.swingMethodologyPanel()).toContainText(
      /Bearish-swing/i,
    );

    await aa.swingPill("sideways").click();
    await expect(aa.swingPill("sideways")).toHaveAttribute(
      "aria-selected", "true",
    );
    await expect(aa.swingMethodologyPanel()).toContainText(
      /Sideways-swing/i,
    );
  });

  test(
    "methodology toggle collapses and persists across reload",
    async ({ page }) => {
      const aa = new AdvancedAnalyticsPage(page);
      await aa.gotoSwingSetups();
      await expect(aa.swingMethodologyPanel()).toBeVisible({
        timeout: 10_000,
      });

      // Toggle to collapse — heading line stays, gates list
      // disappears.
      await aa.swingMethodologyToggle().click();
      await expect(
        page.getByText("Gates (all must hold):"),
      ).toBeHidden();

      // Reload — the seen flag should keep it collapsed.
      await page.reload();
      await expect(aa.swingMethodologyPanel()).toBeVisible({
        timeout: 10_000,
      });
      await expect(
        page.getByText("Gates (all must hold):"),
      ).toBeHidden();
    },
  );
});
