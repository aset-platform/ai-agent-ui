/**
 * Dash-specific wait utilities for Playwright.
 *
 * Plotly Dash re-renders DOM fragments via server callbacks.
 * Standard ``page.waitForNavigation()`` does not work for most
 * Dash interactions — these helpers wait for DOM signals instead.
 */

import { type Locator, type Page, expect } from "@playwright/test";

/**
 * Wait for a Dash callback result by watching a DOM element.
 *
 * Dash callbacks replace the ``children`` of a target element.
 * This helper waits until the element contains the expected text.
 */
export async function waitForDashCallback(
  locator: Locator,
  expectedText: string,
  timeout = 15_000,
): Promise<void> {
  await expect(locator).toContainText(expectedText, { timeout });
}

/**
 * Wait for a Plotly chart to finish rendering.
 *
 * Plotly injects an element with class ``.js-plotly-plot``
 * inside the container once the chart is drawn.
 */
export async function waitForPlotlyChart(
  page: Page,
  containerSelector: string,
  timeout = 15_000,
): Promise<void> {
  await page
    .locator(`${containerSelector} .js-plotly-plot`)
    .waitFor({ state: "visible", timeout });
}

/**
 * Wait for the Dash loading spinner to appear then disappear.
 *
 * Some fast callbacks never show a spinner, so the initial
 * "visible" wait has a short timeout that is silently ignored.
 */
export async function waitForDashLoading(
  page: Page,
  timeout = 30_000,
): Promise<void> {
  const spinner = page.locator("._dash-loading");
  try {
    await spinner.waitFor({ state: "visible", timeout: 3_000 });
    await spinner.waitFor({ state: "hidden", timeout });
  } catch {
    // Spinner may never appear for fast callbacks — that is OK.
  }
}

/**
 * Wait for a Dash ``dcc.Store`` to have a specific value.
 *
 * Reads the store's ``data`` property via JavaScript.
 */
export async function waitForStoreValue(
  page: Page,
  storeId: string,
  expected: unknown,
  timeout = 10_000,
): Promise<void> {
  await page.waitForFunction(
    ([id, val]) => {
      const el = document.getElementById(id as string);
      if (!el) return false;
      try {
        const data = JSON.parse(
          el.getAttribute("data-dash-is-loading") === "false"
            ? (el as HTMLElement).innerText || "{}"
            : "{}",
        );
        return JSON.stringify(data) === JSON.stringify(val);
      } catch {
        return false;
      }
    },
    [storeId, expected],
    { timeout },
  );
}
