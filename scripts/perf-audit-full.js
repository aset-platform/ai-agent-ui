#!/usr/bin/env node
/**
 * Full-surface performance audit — 63 audit points.
 *
 * Sections:
 *   1. Page Loads (10 routes)
 *   2. Tab Switches (21 tabs)
 *   3. Modals (8 modals)
 *   4. Interactive Controls (24 controls)
 *
 * Usage:
 *   node scripts/perf-audit-full.js
 *   cd frontend && npm run perf:full
 *
 * Env vars:
 *   PERF_TEST_EMAIL    — general user email (required)
 *   PERF_TEST_PASSWORD — general user password (required)
 *   PERF_ADMIN_EMAIL   — admin user email (optional)
 *   PERF_ADMIN_PASSWORD — admin user password (optional)
 *   PERF_PORT          — frontend port (default: 3000)
 */

const path = require("path");

// Resolve Playwright from e2e/node_modules
const e2eDir = path.join(__dirname, "..", "e2e");
let chromium;
try {
  chromium = require(
    path.join(e2eDir, "node_modules", "playwright"),
  ).chromium;
} catch {
  const frontendDir = path.join(
    __dirname, "..", "frontend",
  );
  chromium = require(
    path.join(
      frontendDir, "node_modules", "playwright",
    ),
  ).chromium;
}

const { BASE, TIMEOUTS } = require("./perf/config");
const { login } = require("./perf/auth");
const { scoreOverall } = require("./perf/scoring");
const reporter = require("./perf/reporters/console");
const jsonReporter = require("./perf/reporters/json");

const pages = require("./perf/sections/pages");
const tabs = require("./perf/sections/tabs");
const modals = require("./perf/sections/modals");
const interactive = require("./perf/sections/interactive");

async function main() {
  const email = process.env.PERF_TEST_EMAIL;
  const password = process.env.PERF_TEST_PASSWORD;
  const adminEmail = process.env.PERF_ADMIN_EMAIL;
  const adminPassword = process.env.PERF_ADMIN_PASSWORD;

  if (!email || !password) {
    console.error(
      "Set PERF_TEST_EMAIL and PERF_TEST_PASSWORD",
    );
    process.exit(1);
  }

  const creds = { email, password };
  const adminCreds = adminEmail && adminPassword
    ? { email: adminEmail, password: adminPassword }
    : null;

  const totalPoints =
    10 + 21 + 8 + 24; // approximate
  reporter.header(
    "Full Surface Performance Audit", totalPoints,
  );

  if (!adminCreds) {
    console.log(
      "  \x1b[33m⚠ PERF_ADMIN_EMAIL not set"
      + " — admin audit points will be skipped\x1b[0m",
    );
  }

  // Launch browser
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox"],
  });

  const context = await browser.newContext();
  const page = await context.newPage();

  // Enable CDP for TaskDuration metrics
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");

  // Login
  console.log("\n  Logging in...");
  const ok = await login(
    page, BASE, email, password,
  );
  if (!ok) {
    console.error(
      "  \x1b[31m✗ Login failed — check credentials"
      + "\x1b[0m",
    );
    await browser.close();
    process.exit(1);
  }
  console.log("  ✓ Logged in → " + page.url());

  const startTime = Date.now();
  let allResults = [];

  // Section 1: Pages
  const pageResults = await pages.run(
    page, cdp, creds, adminCreds,
  );
  allResults.push(...pageResults);

  // Section 2: Tabs
  const tabResults = await tabs.run(
    page, cdp, creds, adminCreds,
  );
  allResults.push(...tabResults);

  // Section 3: Modals
  const modalResults = await modals.run(
    page, cdp, creds, adminCreds,
  );
  allResults.push(...modalResults);

  // Section 4: Interactive
  const interactiveResults = await interactive.run(
    page, cdp, creds, adminCreds,
  );
  allResults.push(...interactiveResults);

  await browser.close();

  // Calculate section averages
  const avg = (arr) => {
    const valid = arr.filter(
      (r) => !r.skipped && r.score !== null,
    );
    if (valid.length === 0) return 100;
    return Math.round(
      valid.reduce((s, r) => s + r.score, 0)
      / valid.length,
    );
  };

  const sectionScores = {
    pages: avg(pageResults),
    tabs: avg(tabResults),
    modals: avg(modalResults),
    interactive: avg(interactiveResults),
  };

  const overall = scoreOverall(sectionScores);

  // Reports
  reporter.summary(allResults, sectionScores, overall);
  jsonReporter.writeBaseline(
    allResults, sectionScores, overall,
  );

  const elapsed = Math.round(
    (Date.now() - startTime) / 1000,
  );
  console.log(
    `\n  Completed in ${elapsed}s\n`,
  );

  const failed = allResults.filter(
    (r) => !r.passed && !r.skipped,
  );
  process.exit(failed.length > 0 ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
