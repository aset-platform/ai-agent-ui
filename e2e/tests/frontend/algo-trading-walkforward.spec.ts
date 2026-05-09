/**
 * E2E smoke for the Walk-forward CV sub-tab — Slice V2-2.
 *
 * Test 1: smoke — BacktestTab now shows sub-tab strip and both
 *   sub-tabs are accessible; single-run sub-tab still works.
 *
 * Test 2: walk-forward form is visible when the sub-tab is active.
 *
 * Test 3: kick off a 3-window walk-forward via mocked API,
 *   wait for completion, assert aggregate cards + 3 equity
 *   curve series render.
 *
 * The API routes are mocked via page.route() so the test runs
 * deterministically without a real OHLCV database or strategy.
 */

import { expect, test } from "@playwright/test";

import { FE } from "../../utils/selectors";

// Must match NEXT_PUBLIC_BACKEND_URL (localhost, not 127.0.0.1)
// per CLAUDE.md §6.3 cookie hostname mismatch rule.
const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8181";

const WF_RUN_ID = "00000000-0000-0000-0000-000000000001";
const STRATEGY_ID = "00000000-0000-0000-0000-000000000002";

/** Synthetic 3-window result the mock API returns. */
function makeWalkForwardResult(status: string) {
  const windows = [0, 1, 2].map((i) => ({
    window_index: i,
    run_id: `00000000-0000-0000-0000-00000000000${i + 3}`,
    train_start: `2024-0${i + 1}-01`,
    train_end: `2024-0${i + 1}-30`,
    test_start: `2024-0${i + 2}-01`,
    test_end: `2024-0${i + 2}-28`,
    status: "completed",
    total_pnl_pct: (i * 2 - 1).toFixed(2),
    win_rate_pct: "55.00",
    max_drawdown_pct: "3.00",
    equity_curve: [
      { bar_date: `2024-0${i + 2}-01`, equity_inr: "100000" },
      { bar_date: `2024-0${i + 2}-15`, equity_inr: "101000" },
      { bar_date: `2024-0${i + 2}-28`, equity_inr: "102000" },
    ],
    error_text: null,
  }));
  return {
    walkforward_run_id: WF_RUN_ID,
    strategy_id: STRATEGY_ID,
    status,
    period_start: "2024-01-01",
    period_end: "2024-04-28",
    train_days: 30,
    test_days: 28,
    step_days: 30,
    window_summaries: status === "completed" ? windows : [],
    aggregate:
      status === "completed"
        ? {
            avg_win_rate_pct: "55.00",
            avg_pnl_pct: "1.00",
            avg_max_drawdown_pct: "3.00",
            std_pnl_pct: "2.00",
            window_count: 3,
            completed_count: 3,
          }
        : null,
    error_text: null,
  };
}

test.describe("Algo Trading — Walk-forward CV (V2-2)", () => {
  test("backtest tab shows sub-tab strip with both sub-tabs", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=backtest");
    await expect(
      page.getByTestId(FE.algoBacktestTab),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoBacktestSubTabStrip),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoBacktestSubTabSingle),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoBacktestSubTabWalkforward),
    ).toBeVisible();
    // Single-run form is still visible on the default sub-tab
    await expect(
      page.getByTestId(FE.algoBacktestRunForm),
    ).toBeVisible();
  });

  test("clicking walk-forward sub-tab shows config form", async ({
    page,
  }) => {
    await page.goto("/algo-trading?tab=backtest");
    await page
      .getByTestId(FE.algoBacktestSubTabWalkforward)
      .click();
    await expect(
      page.getByTestId(FE.algoWalkforwardSubTab),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoWalkforwardRunForm),
    ).toBeVisible();
    await expect(
      page.getByTestId(FE.algoWalkforwardSubmit),
    ).toBeVisible();
    // Single-run form should be gone
    await expect(
      page.getByTestId(FE.algoBacktestRunForm),
    ).not.toBeVisible();
  });

  test(
    "submitting walk-forward form shows results with 3 window rows",
    async ({ page }) => {
      // ── Mock API ────────────────────────────────────────────
      // strategies list (for the form dropdown)
      // The hook expects { strategies: [...] } per useStrategies.ts
      await page.route(
        `${BACKEND_URL}/v1/algo/strategies`,
        async (route) => {
          if (route.request().method() !== "GET") {
            await route.continue();
            return;
          }
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              strategies: [
                {
                  id: STRATEGY_ID,
                  name: "Test Golden Cross",
                  description: "",
                  created_at: "2024-01-01T00:00:00Z",
                  updated_at: "2024-01-01T00:00:00Z",
                },
              ],
            }),
          });
        },
      );

      // walk-forward run list (empty initially)
      await page.route(
        `${BACKEND_URL}/v1/algo/walkforward/runs*`,
        async (route, request) => {
          if (
            request.method() === "GET" &&
            !request.url().includes(WF_RUN_ID)
          ) {
            await route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify([]),
            });
          } else {
            await route.continue();
          }
        },
      );

      // POST walk-forward/run → 202
      await page.route(
        `${BACKEND_URL}/v1/algo/walkforward/run`,
        async (route) => {
          await route.fulfill({
            status: 202,
            contentType: "application/json",
            body: JSON.stringify({
              walkforward_run_id: WF_RUN_ID,
              status: "pending",
            }),
          });
        },
      );

      // GET /runs/{id} — start as pending, then complete
      let callCount = 0;
      await page.route(
        `${BACKEND_URL}/v1/algo/walkforward/runs/${WF_RUN_ID}`,
        async (route) => {
          callCount++;
          const status = callCount <= 1 ? "running" : "completed";
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(makeWalkForwardResult(status)),
          });
        },
      );

      // ── Navigate + click sub-tab ─────────────────────────────
      await page.goto("/algo-trading?tab=backtest");
      await page
        .getByTestId(FE.algoBacktestSubTabWalkforward)
        .click();
      await expect(
        page.getByTestId(FE.algoWalkforwardRunForm),
      ).toBeVisible();

      // Select strategy
      await page
        .getByTestId(FE.algoWalkforwardStrategySelect)
        .selectOption(STRATEGY_ID);

      // Submit
      await page
        .getByTestId(FE.algoWalkforwardSubmit)
        .click();

      // Wait for run to complete (SWR polls every 2s)
      await expect(
        page.getByTestId(FE.algoWalkforwardAggCards),
      ).toBeVisible({ timeout: 10_000 });

      // 3 equity curves container
      await expect(
        page.getByTestId(FE.algoWalkforwardCurves),
      ).toBeVisible();

      // Window table with 3 rows
      await expect(
        page.getByTestId(FE.algoWalkforwardWindowTable),
      ).toBeVisible();
      const rows = page.getByTestId(/^walkforward-window-row-/);
      await expect(rows).toHaveCount(3);
    },
  );
});
