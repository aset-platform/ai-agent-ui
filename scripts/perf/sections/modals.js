/**
 * Section 3: Modal open audits.
 */

const {
  MODALS, BASE, MODAL_BUDGET,
} = require("../config");
const { measureModalOpen, clearPerfEntries } = require("../metrics");
const { scoreModalOpen } = require("../scoring");
const reporter = require("../reporters/console");

async function run(page, cdp, creds, adminCreds) {
  reporter.section("SECTION 3: Modals");
  const results = [];
  let currentPage = "";

  for (const modal of MODALS) {
    // Skip admin modals if no admin creds
    if (modal.auth === "admin" && !adminCreds) {
      results.push({
        id: modal.id,
        type: "modal",
        score: null,
        budget: MODAL_BUDGET,
        passed: true,
        skipped: true,
      });
      reporter.pageResult(results[results.length - 1]);
      continue;
    }

    // Navigate to the modal's trigger page if needed
    if (currentPage !== modal.page) {
      await page.goto(`${BASE}${modal.page}`, {
        waitUntil: "networkidle",
        timeout: 30000,
      });
      await page.waitForTimeout(1500);
      currentPage = modal.page;
    }

    await clearPerfEntries(page);
    const { metrics, error } = await measureModalOpen(
      page, cdp, modal.triggerSteps, modal.waitFor,
    );

    const score = error
      ? 0
      : scoreModalOpen(metrics);

    results.push({
      id: modal.id,
      type: "modal",
      label: modal.label,
      score,
      budget: MODAL_BUDGET,
      passed: !error && score >= MODAL_BUDGET,
      metrics,
      error,
    });
    reporter.pageResult(results[results.length - 1]);

    // Close modal — try Escape, then reload page if stuck
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);

    // Verify modal is closed — if overlay still present,
    // reload the page to reset state
    const overlay = await page.$(
      ".fixed.inset-0, [data-testid*='modal']",
    );
    if (overlay) {
      await page.goto(`${BASE}${modal.page}`, {
        waitUntil: "networkidle",
        timeout: 15000,
      });
      await page.waitForTimeout(1000);
    }
  }

  return results;
}

module.exports = { run };
