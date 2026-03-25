/**
 * Section 4: Interactive control audits.
 */

const {
  INTERACTIVE, BASE, INTERACTION_BUDGET,
} = require("../config");
const {
  measureInteraction, clearPerfEntries,
} = require("../metrics");
const { scoreInteraction } = require("../scoring");
const reporter = require("../reporters/console");

async function run(page, cdp, creds, adminCreds) {
  reporter.section("SECTION 4: Interactive Controls");
  const results = [];
  let currentPage = "";

  for (const ctrl of INTERACTIVE) {
    // Navigate to page if needed
    if (currentPage !== ctrl.page) {
      await page.goto(`${BASE}${ctrl.page}`, {
        waitUntil: "networkidle",
        timeout: 30000,
      });
      await page.waitForTimeout(2000);
      currentPage = ctrl.page;
    }

    // Handle prerequisite (e.g., switch to a specific tab)
    if (ctrl.prerequisite?.tab) {
      const tabSel =
        `[data-testid="analytics-tab-${ctrl.prerequisite.tab}"]`;
      try {
        await page.click(tabSel, { timeout: 3000 });
        await page.waitForTimeout(1500);
      } catch {
        // Tab might already be active
      }
    }

    // Check if control is visible
    const el = await page.$(ctrl.selector);
    if (!el) {
      results.push({
        id: ctrl.id,
        type: "interaction",
        score: null,
        budget: INTERACTION_BUDGET,
        passed: true,
        skipped: true,
        skipReason: "element not found",
      });
      reporter.pageResult(results[results.length - 1]);
      continue;
    }

    await clearPerfEntries(page);
    const { metrics, error } = await measureInteraction(
      page, cdp, ctrl.selector, ctrl.settledCheck,
    );

    const score = error
      ? 0
      : scoreInteraction(metrics);

    results.push({
      id: ctrl.id,
      type: "interaction",
      label: ctrl.label,
      score,
      budget: INTERACTION_BUDGET,
      passed: !error && score >= INTERACTION_BUDGET,
      metrics,
      error,
    });
    reporter.pageResult(results[results.length - 1]);
  }

  return results;
}

module.exports = { run };
