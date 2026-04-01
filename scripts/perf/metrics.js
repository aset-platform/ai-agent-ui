/**
 * Metric collection functions for 4 audit types.
 *
 * Each function returns:
 *   { metrics: {...}, error?: string }
 */

const { TIMEOUTS } = require("./config");

/**
 * Clear Performance API entries for a fresh measurement.
 */
async function clearPerfEntries(page) {
  await page.evaluate(() => {
    performance.clearResourceTimings();
    performance.clearMarks();
    performance.clearMeasures();
  });
}

/**
 * Get CDP TaskDuration baseline from a CDP session.
 */
async function getTaskDuration(cdp) {
  const { metrics } = await cdp.send(
    "Performance.getMetrics",
  );
  const td = metrics.find(
    (m) => m.name === "TaskDuration",
  );
  return (td?.value || 0) * 1000; // ms
}

/**
 * Collect Web Vitals from page via Performance API.
 */
async function collectWebVitals(page) {
  return page.evaluate(() => {
    const result = {
      fcp: 0,
      lcp: 0,
      cls: 0,
    };

    const fcp = performance.getEntriesByName(
      "first-contentful-paint",
    )[0];
    if (fcp) result.fcp = Math.round(fcp.startTime);

    const lcpEntries = performance.getEntriesByType(
      "largest-contentful-paint",
    );
    if (lcpEntries.length > 0) {
      result.lcp = Math.round(
        lcpEntries[lcpEntries.length - 1].startTime,
      );
    }

    const clsEntries = performance.getEntriesByType(
      "layout-shift",
    );
    let cls = 0;
    for (const e of clsEntries) {
      if (!e.hadRecentInput) cls += e.value;
    }
    result.cls = cls;

    return result;
  });
}

// ─────────────────────────────────────────────────
// Type A: Full Page Load
// ─────────────────────────────────────────────────

async function measurePageLoad(page, cdp, url) {
  try {
    const tbtBefore = await getTaskDuration(cdp);

    await page.goto(url, {
      waitUntil: "networkidle",
      timeout: TIMEOUTS.pageLoad,
    });
    await page.waitForTimeout(TIMEOUTS.settleDelay);

    const vitals = await collectWebVitals(page);
    const tbtAfter = await getTaskDuration(cdp);

    return {
      metrics: {
        fcp_ms: vitals.fcp,
        lcp_ms: vitals.lcp,
        cls: vitals.cls,
        tbt_ms: Math.round(tbtAfter - tbtBefore),
      },
    };
  } catch (err) {
    return { metrics: null, error: err.message };
  }
}

// ─────────────────────────────────────────────────
// Type B: Tab Switch
// ─────────────────────────────────────────────────

async function measureTabSwitch(page, cdp, tabSelector, waitForSelector) {
  try {
    const tbtBefore = await getTaskDuration(cdp);
    const startTime = Date.now();

    // Click the tab
    await page.click(tabSelector, {
      timeout: 5000,
    });

    // Wait for content to appear
    // waitForSelector may be comma-separated alternatives
    const selectors = waitForSelector.split(",").map(
      (s) => s.trim(),
    );
    await Promise.race(
      selectors.map((sel) =>
        page.waitForSelector(sel, {
          state: "visible",
          timeout: TIMEOUTS.tabSwitch,
        }).catch(() => null),
      ),
    );

    await page.waitForTimeout(500);
    const switchMs = Date.now() - startTime;
    const tbtAfter = await getTaskDuration(cdp);

    // CLS during switch
    const cls = await page.evaluate(() => {
      const entries = performance.getEntriesByType(
        "layout-shift",
      );
      let score = 0;
      for (const e of entries) {
        if (!e.hadRecentInput) score += e.value;
      }
      return score;
    });

    return {
      metrics: {
        switch_ms: switchMs,
        cls,
        tbt_ms: Math.round(tbtAfter - tbtBefore),
      },
    };
  } catch (err) {
    return { metrics: null, error: err.message };
  }
}

// ─────────────────────────────────────────────────
// Type C: Modal Open
// ─────────────────────────────────────────────────

async function measureModalOpen(page, cdp, triggerSteps, waitForSelector) {
  try {
    const tbtBefore = await getTaskDuration(cdp);
    const startTime = Date.now();

    // Execute trigger steps (click sequences)
    for (const step of triggerSteps) {
      if (step.selector) {
        await page.click(step.selector, {
          timeout: 5000,
        });
      } else if (step.text) {
        await page.getByText(step.text, { exact: false })
          .first()
          .click({ timeout: 5000 });
      }
      await page.waitForTimeout(300);
    }

    // Wait for modal to appear
    const selectors = waitForSelector.split(",").map(
      (s) => s.trim(),
    );
    await Promise.race(
      selectors.map((sel) =>
        page.waitForSelector(sel, {
          state: "visible",
          timeout: TIMEOUTS.modalOpen,
        }).catch(() => null),
      ),
    );

    const openMs = Date.now() - startTime;
    await page.waitForTimeout(500);
    const tbtAfter = await getTaskDuration(cdp);

    const cls = await page.evaluate(() => {
      const entries = performance.getEntriesByType(
        "layout-shift",
      );
      let score = 0;
      for (const e of entries) {
        if (!e.hadRecentInput) score += e.value;
      }
      return score;
    });

    return {
      metrics: {
        open_ms: openMs,
        cls,
        tbt_ms: Math.round(tbtAfter - tbtBefore),
      },
    };
  } catch (err) {
    return { metrics: null, error: err.message };
  }
}

// ─────────────────────────────────────────────────
// Type D: Interactive Control
// ─────────────────────────────────────────────────

async function measureInteraction(page, cdp, selector, settledCheck) {
  try {
    const tbtBefore = await getTaskDuration(cdp);
    const startTime = Date.now();

    await page.click(selector, { timeout: 5000 });

    // Wait for settled based on type
    switch (settledCheck) {
      case "chart-redraw":
        // Wait for canvas to re-render
        await page.waitForTimeout(1500);
        break;
      case "content-update":
        // Wait for network to settle
        await page.waitForLoadState("networkidle", {
          timeout: 10000,
        }).catch(() => {});
        await page.waitForTimeout(500);
        break;
      case "layout-settle":
        // Wait for CSS transition (sidebar/chat)
        await page.waitForTimeout(500);
        break;
      default:
        await page.waitForTimeout(1000);
    }

    const responseMs = Date.now() - startTime;
    const tbtAfter = await getTaskDuration(cdp);

    const cls = await page.evaluate(() => {
      const entries = performance.getEntriesByType(
        "layout-shift",
      );
      let score = 0;
      for (const e of entries) {
        if (!e.hadRecentInput) score += e.value;
      }
      return score;
    });

    return {
      metrics: {
        response_ms: responseMs,
        cls,
        tbt_ms: Math.round(tbtAfter - tbtBefore),
      },
    };
  } catch (err) {
    return { metrics: null, error: err.message };
  }
}

module.exports = {
  clearPerfEntries,
  measurePageLoad,
  measureTabSwitch,
  measureModalOpen,
  measureInteraction,
};
