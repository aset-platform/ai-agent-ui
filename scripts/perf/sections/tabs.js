/**
 * Section 2: Tab switch audits.
 * Groups tabs by parent page to avoid redundant navigations.
 */

const { TABS, BASE, TAB_BUDGET } = require("../config");
const { measureTabSwitch, clearPerfEntries } = require("../metrics");
const { scoreTabSwitch } = require("../scoring");
const reporter = require("../reporters/console");

async function run(page, cdp, creds, adminCreds) {
  reporter.section("SECTION 2: Tab Switches");
  const results = [];

  // Group tabs by parent page
  const groups = {};
  for (const tab of TABS) {
    const key = tab.page;
    if (!groups[key]) groups[key] = [];
    groups[key].push(tab);
  }

  for (const [pagePath, pageTabs] of Object.entries(groups)) {
    // Skip admin tabs if no admin creds
    if (
      pageTabs[0].auth === "admin" && !adminCreds
    ) {
      for (const tab of pageTabs) {
        results.push({
          id: tab.id,
          type: "tab",
          score: null,
          budget: TAB_BUDGET,
          passed: true,
          skipped: true,
        });
        reporter.pageResult(
          results[results.length - 1],
        );
      }
      continue;
    }

    // Skip modal-triggered tabs (profile modal)
    if (pageTabs[0].modalTrigger) {
      // Handle profile modal tabs separately
      for (const tab of pageTabs) {
        results.push({
          id: tab.id,
          type: "tab",
          score: null,
          budget: TAB_BUDGET,
          passed: true,
          skipped: true,
          skipReason: "modal-tab",
        });
        reporter.pageResult(
          results[results.length - 1],
        );
      }
      continue;
    }

    // Navigate to parent page
    await page.goto(`${BASE}${pagePath}`, {
      waitUntil: "networkidle",
      timeout: 30000,
    });
    await page.waitForTimeout(2000);

    // Click each tab in sequence
    for (const tab of pageTabs) {
      if (!tab.selector) continue;
      await clearPerfEntries(page);

      const { metrics, error } = await measureTabSwitch(
        page, cdp, tab.selector, tab.waitFor,
      );

      const score = error
        ? 0
        : scoreTabSwitch(metrics);

      results.push({
        id: tab.id,
        type: "tab",
        score,
        budget: TAB_BUDGET,
        passed: !error && score >= TAB_BUDGET,
        metrics,
        error,
      });
      reporter.pageResult(results[results.length - 1]);
    }
  }

  return results;
}

module.exports = { run };
