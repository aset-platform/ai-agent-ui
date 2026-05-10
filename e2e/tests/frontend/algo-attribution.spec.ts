import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("REGIME-6 — AttributionPanel", () => {
  test(
    "navigates to paper tab + Live mode mounts the panel without crash",
    async ({ page }) => {
      await page.goto("/algo-trading?tab=paper");

      // The trading mode toggle is the canonical anchor for the
      // Paper tab. Wait for it before flipping to Live (default
      // is Live per PaperTab.tsx, but click defensively in case
      // the default ever changes).
      await expect(
        page.getByTestId("trading-mode-toggle"),
      ).toBeVisible();
      await page.getByTestId("trading-mode-live").click();

      // Either the AttributionPanel mounted (strategy was
      // pre-selected) or we see the live-no-strategy guidance
      // message (no strategy picked yet). Both are valid states
      // — the spec only verifies we can navigate without crash.
      // Both may be visible at once (panel renders even with no
      // strategy, surfacing its own empty-state); .or().first()
      // sidesteps strict-mode for that case.
      const panel = page.getByTestId(FE.attributionPanel);
      const noStrategy = page.getByTestId("live-no-strategy-msg");

      await expect(panel.or(noStrategy).first()).toBeVisible({
        timeout: 15_000,
      });

      // If the panel did mount, verify the sub-tab strip is
      // present (sanity check for the inner UI).
      if (await panel.count() > 0) {
        await expect(
          page.getByTestId(FE.attributionSubtabStrip),
        ).toBeVisible();
      }
    },
  );
});
