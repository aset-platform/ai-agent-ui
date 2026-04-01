/**
 * Section 1: Full page load audits.
 */

const { PAGES, BASE } = require("../config");
const { measurePageLoad, clearPerfEntries } = require("../metrics");
const { scorePageLoad } = require("../scoring");
const { ensureAuth } = require("../auth");
const reporter = require("../reporters/console");

async function run(page, cdp, creds, adminCreds) {
  reporter.section("SECTION 1: Page Loads");
  const results = [];

  for (const route of PAGES) {
    // Skip admin routes if no admin creds
    if (route.auth === "admin" && !adminCreds) {
      results.push({
        id: route.id,
        type: "page",
        score: null,
        budget: route.budget,
        passed: true,
        skipped: true,
      });
      reporter.pageResult(results[results.length - 1]);
      continue;
    }

    await clearPerfEntries(page);
    const url = `${BASE}${route.path}`;
    const { metrics, error } = await measurePageLoad(
      page, cdp, url,
    );

    // Auth recovery
    if (
      !error
      && page.url().includes("/login")
      && route.auth
    ) {
      const email = route.auth === "admin"
        ? adminCreds.email : creds.email;
      const pwd = route.auth === "admin"
        ? adminCreds.password : creds.password;
      await ensureAuth(page, BASE, email, pwd);
      // Retry once
      await clearPerfEntries(page);
      const retry = await measurePageLoad(
        page, cdp, url,
      );
      if (!retry.error) {
        const score = scorePageLoad(retry.metrics);
        results.push({
          id: route.id,
          type: "page",
          score,
          budget: route.budget,
          passed: score >= route.budget,
          metrics: retry.metrics,
        });
        reporter.pageResult(results[results.length - 1]);
        continue;
      }
    }

    const score = error ? 0 : scorePageLoad(metrics);
    results.push({
      id: route.id,
      type: "page",
      score,
      budget: route.budget,
      passed: !error && score >= route.budget,
      metrics,
      error,
    });
    reporter.pageResult(results[results.length - 1]);
  }

  return results;
}

module.exports = { run };
