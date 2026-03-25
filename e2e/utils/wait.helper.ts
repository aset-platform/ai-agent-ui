/**
 * Wait utilities for Next.js Playwright tests.
 *
 * Provides helpers for TradingView charts (canvas-based),
 * Plotly charts, API response interception, and refresh
 * button state transitions.
 */

import { type Page, expect } from "@playwright/test";

/**
 * Wait for a TradingView lightweight-chart to render its canvas.
 *
 * TradingView draws on a ``<canvas>`` element inside the
 * container identified by ``data-testid``.
 */
export async function waitForTradingViewChart(
  page: Page,
  testId: string,
  timeout = 15_000,
): Promise<void> {
  const container = page.getByTestId(testId);
  await container.waitFor({ state: "visible", timeout });
  await container
    .locator("canvas")
    .first()
    .waitFor({ state: "visible", timeout });
}

/**
 * Wait for a Plotly chart to finish rendering inside a
 * Next.js component identified by ``data-testid``.
 */
export async function waitForPlotlyChart(
  page: Page,
  testId: string,
  timeout = 15_000,
): Promise<void> {
  const container = page.getByTestId(testId);
  await container.waitFor({ state: "visible", timeout });
  await container
    .locator(".js-plotly-plot")
    .waitFor({ state: "visible", timeout });
}

/**
 * Intercept an API response and return its JSON body.
 *
 * Useful for validating the data that feeds a chart or table
 * without relying on DOM scraping.
 */
export async function waitForApiResponse<T = unknown>(
  page: Page,
  urlPattern: string | RegExp,
  timeout = 15_000,
): Promise<T> {
  const response = await page.waitForResponse(
    (res) => {
      const url = res.url();
      if (typeof urlPattern === "string") {
        return url.includes(urlPattern) && res.status() === 200;
      }
      return urlPattern.test(url) && res.status() === 200;
    },
    { timeout },
  );
  return response.json() as Promise<T>;
}

/**
 * Wait for a refresh button to cycle through its states.
 *
 * Watches the refresh icon ``data-testid`` for the
 * ``data-state`` attribute to transition from "pending"
 * to "success" or "error".
 */
export async function waitForRefreshComplete(
  page: Page,
  iconTestId: string,
  timeout = 180_000,
): Promise<string> {
  const icon = page.getByTestId(iconTestId);

  // Wait for pending state to appear
  await expect(icon).toHaveAttribute(
    "data-state",
    "pending",
    { timeout: 5_000 },
  ).catch(() => {
    // May already be past pending if very fast
  });

  // Wait for terminal state
  await page.waitForFunction(
    (tid) => {
      const el = document.querySelector(
        `[data-testid="${tid}"]`,
      );
      if (!el) return false;
      const state = el.getAttribute("data-state");
      return state === "success" || state === "error";
    },
    iconTestId,
    { timeout },
  );

  const state = await icon.getAttribute("data-state");
  return state || "unknown";
}

/**
 * Wait for a Next.js page to be fully hydrated and idle.
 *
 * Waits until there are no pending network requests and the
 * page has reached "networkidle" state.
 */
export async function waitForPageReady(
  page: Page,
  timeout = 15_000,
): Promise<void> {
  await page.waitForLoadState("networkidle", { timeout });
}
