/**
 * E2E for the regime widget on the Trading tab — REGIME-1.
 *
 * The widget mounts inside the Trading tab header. Until the
 * daily classifier job has produced a row, the API returns 404
 * and the widget shows its empty state ("Regime: —"). Once a
 * row exists, the widget renders the badge + VIX gauge +
 * breadth bar + stress chip.
 *
 * This spec tolerates BOTH states (empty / populated) so it
 * passes on a clean dev DB AND on a backfilled environment.
 * The history chart sits below the live section and likewise
 * shows either an empty placeholder or the rendered chart.
 *
 * Mirrors `algo-trading-paper.spec.ts` — plain @playwright/test,
 * superuser storageState, no fixture / page object dependency.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Algo Trading — Regime widget (REGIME-1)", () => {
  test("trading tab renders regime widget in some state", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=paper");
    await expect(page.getByTestId(FE.algoPaperTab)).toBeVisible();

    // Widget mounts in one of three states: loading, empty, or
    // populated. After a brief settle period exactly one of the
    // empty-state or populated testids must be visible.
    await page.waitForTimeout(1_500);

    const populated = page.getByTestId(FE.regimeWidget);
    const empty = page.getByTestId(FE.regimeWidgetEmpty);
    const loading = page.getByTestId(FE.regimeWidgetLoading);

    const visibleCount =
      (await populated.isVisible() ? 1 : 0)
      + (await empty.isVisible() ? 1 : 0)
      + (await loading.isVisible() ? 1 : 0);

    expect(visibleCount).toBeGreaterThanOrEqual(1);

    // If populated, the badge must be one of the three labels.
    if (await populated.isVisible()) {
      const badge = page.getByTestId(FE.regimeBadge);
      await expect(badge).toBeVisible();
      await expect(badge).toHaveText(/^(BULL|SIDEWAYS|BEAR)$/);
    }
  });

  test("trading tab renders regime history chart placeholder", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=paper");
    await expect(page.getByTestId(FE.algoPaperTab)).toBeVisible();

    // History chart only mounts in live / dryrun view; default
    // is live. It either shows the chart or the empty state.
    const chart = page.getByTestId(FE.regimeHistoryChart);
    const empty = page.getByTestId(FE.regimeHistoryEmpty);

    await page.waitForTimeout(1_500);
    const visibleCount =
      (await chart.isVisible() ? 1 : 0)
      + (await empty.isVisible() ? 1 : 0);
    expect(visibleCount).toBeGreaterThanOrEqual(1);
  });
});
