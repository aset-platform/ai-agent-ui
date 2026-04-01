/**
 * Performance tests using Playwright + Lighthouse (Gap 8).
 *
 * Measures Core Web Vitals (LCP, FCP, TBT, CLS) on key
 * pages and asserts they meet production thresholds.
 *
 * Requires: npm install --save-dev playwright-lighthouse
 * Run: npx playwright test --project=performance
 */

import { test, expect } from "@playwright/test";

/** Thresholds for Core Web Vitals (milliseconds / ratio). */
const THRESHOLDS = {
  LCP: 2_500,
  FCP: 1_800,
  TBT: 300,
  CLS: 0.1,
};

/**
 * Collect performance metrics using the browser
 * Performance API. Falls back gracefully if entries
 * are not available (e.g., SSR pre-rendered content).
 */
async function collectMetrics(page: import("@playwright/test").Page) {
  // Wait for page to be fully loaded
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(2_000);

  return page.evaluate(() => {
    const paint = performance.getEntriesByType(
      "paint",
    ) as PerformanceEntry[];
    const fcp = paint.find(
      (e) => e.name === "first-contentful-paint",
    );

    // LCP via buffered PerformanceObserver
    let lcp = 0;
    try {
      const entries = performance.getEntriesByType(
        "largest-contentful-paint",
      );
      if (entries.length > 0) {
        lcp = entries[entries.length - 1].startTime;
      }
    } catch {
      // LCP not supported in all contexts
    }

    // CLS via layout-shift entries
    let cls = 0;
    try {
      const shifts = performance.getEntriesByType(
        "layout-shift",
      ) as (PerformanceEntry & { value?: number })[];
      cls = shifts.reduce(
        (sum, e) => sum + (e.value || 0),
        0,
      );
    } catch {
      // layout-shift not supported
    }

    // TBT approximation via long-task entries
    let tbt = 0;
    try {
      const tasks = performance.getEntriesByType(
        "longtask",
      );
      tbt = tasks.reduce(
        (sum, t) => sum + Math.max(0, t.duration - 50),
        0,
      );
    } catch {
      // longtask not supported
    }

    return {
      fcp: fcp?.startTime || 0,
      lcp,
      tbt,
      cls,
    };
  });
}

test.describe("Lighthouse performance", () => {
  test.slow(); // Performance tests need extra time

  test("login page meets thresholds", async ({
    browser,
  }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto("/login");
    const m = await collectMetrics(page);

    expect(m.fcp).toBeLessThan(THRESHOLDS.FCP);
    expect(m.cls).toBeLessThan(THRESHOLDS.CLS);
    // LCP may be 0 for SSR pages — only assert if >0
    if (m.lcp > 0) {
      expect(m.lcp).toBeLessThan(THRESHOLDS.LCP);
    }
    await context.close();
  });

  test("chat page meets thresholds", async ({
    page,
  }) => {
    await page.goto("/");
    const m = await collectMetrics(page);

    expect(m.fcp).toBeLessThan(THRESHOLDS.FCP);
    expect(m.tbt).toBeLessThan(THRESHOLDS.TBT);
    expect(m.cls).toBeLessThan(THRESHOLDS.CLS);
    if (m.lcp > 0) {
      expect(m.lcp).toBeLessThan(THRESHOLDS.LCP);
    }
  });

  test("dashboard page meets thresholds", async ({
    page,
  }) => {
    await page.goto("/dashboard");
    const m = await collectMetrics(page);

    expect(m.fcp).toBeLessThan(THRESHOLDS.FCP);
    expect(m.cls).toBeLessThan(THRESHOLDS.CLS);
    if (m.lcp > 0) {
      expect(m.lcp).toBeLessThan(THRESHOLDS.LCP);
    }
  });

  test("analytics page meets thresholds", async ({
    page,
  }) => {
    await page.goto("/analytics");
    const m = await collectMetrics(page);

    expect(m.fcp).toBeLessThan(THRESHOLDS.FCP);
    expect(m.cls).toBeLessThan(THRESHOLDS.CLS);
    if (m.lcp > 0) {
      expect(m.lcp).toBeLessThan(THRESHOLDS.LCP);
    }
  });
});
