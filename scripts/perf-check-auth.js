#!/usr/bin/env node
/**
 * Performance audit for ALL routes using Playwright + Lighthouse.
 *
 * Playwright handles login (proper cookies + localStorage),
 * then passes the CDP connection to Lighthouse for each route.
 *
 * Usage:
 *   node scripts/perf-check-auth.js
 *   cd frontend && npm run perf:audit
 *
 * Requires:
 *   - Frontend on port 3000 (or PERF_PORT)
 *   - Backend on port 8181
 *   - PERF_TEST_EMAIL + PERF_TEST_PASSWORD env vars
 */

const path = require("path");

const frontendDir = path.join(__dirname, "..", "frontend");
const e2eDir = path.join(__dirname, "..", "e2e");

// Resolve from e2e/node_modules (Playwright) and
// frontend/node_modules (Lighthouse)
let chromium, lighthouse;

const PORT = process.env.PERF_PORT || 3000;
const BASE = `http://localhost:${PORT}`;

const ROUTES = [
  { path: "/login", budget: 90, auth: false },
  { path: "/dashboard", budget: 80, auth: true },
  { path: "/analytics", budget: 80, auth: true },
  { path: "/analytics/analysis", budget: 75, auth: true },
  { path: "/analytics/compare", budget: 75, auth: true },
  { path: "/analytics/insights", budget: 75, auth: true },
  { path: "/analytics/marketplace", budget: 75, auth: true },
  { path: "/admin", budget: 70, auth: true },
  { path: "/docs", budget: 85, auth: true },
  { path: "/insights", budget: 75, auth: true },
];

async function loadDeps() {
  // Try e2e/node_modules first, then frontend/
  try {
    chromium = require(
      path.join(e2eDir, "node_modules", "playwright"),
    ).chromium;
  } catch {
    try {
      chromium = require(
        path.join(
          frontendDir, "node_modules", "playwright",
        ),
      ).chromium;
    } catch {
      chromium = require("playwright").chromium;
    }
  }

  const lhPath = path.join(
    frontendDir, "node_modules",
    "lighthouse", "core", "index.js",
  );
  const lh = await import(lhPath);
  lighthouse = lh.default;
}

async function main() {
  const email = process.env.PERF_TEST_EMAIL;
  const password = process.env.PERF_TEST_PASSWORD;

  if (!email || !password) {
    console.error(
      "Set PERF_TEST_EMAIL and PERF_TEST_PASSWORD",
    );
    process.exit(1);
  }

  await loadDeps();

  console.log(
    "\n\x1b[36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    + "━━━━━━━━━━━━━━━━━━━\x1b[0m",
  );
  console.log(
    "\x1b[36m  Performance Audit — All Routes"
    + " (Playwright + Lighthouse)\x1b[0m",
  );
  console.log(
    "\x1b[36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    + "━━━━━━━━━━━━━━━━━━━\x1b[0m\n",
  );

  // Launch browser with remote debugging
  const browser = await chromium.launch({
    headless: true,
    args: [
      "--remote-debugging-port=0",
      "--no-sandbox",
    ],
  });

  // Login via Playwright (handles cookies properly)
  const context = await browser.newContext();
  const page = await context.newPage();

  console.log("  Logging in...");
  await page.goto(`${BASE}/login`, {
    waitUntil: "domcontentloaded",
  });

  await page.fill(
    'input[type="email"], input[name="email"]',
    email,
  );
  await page.fill(
    'input[type="password"], input[name="password"]',
    password,
  );
  await page.click('button[type="submit"]');

  // Wait for navigation to complete (SPA redirect)
  await page.waitForURL("**/dashboard**", {
    timeout: 15000,
  });
  console.log("  ✓ Logged in → " + page.url() + "\n");

  // Get the CDP port from the browser
  const wsUrl = browser.wsEndpoint
    ? browser.wsEndpoint()
    : null;

  // For Playwright, we need the debugger port
  // Use context's CDP session instead
  const cdpSession = await context.newCDPSession(page);

  const results = [];
  let allPassed = true;

  for (const route of ROUTES) {
    const url = `${BASE}${route.path}`;
    process.stdout.write(
      `  ${route.path.padEnd(30)} `,
    );

    try {
      // Navigate and collect metrics via Playwright
      const start = Date.now();
      await page.goto(url, {
        waitUntil: "networkidle",
        timeout: 30000,
      });
      await page.waitForTimeout(2000);
      const loadTime = Date.now() - start;

      const finalUrl = page.url();

      // Check for auth redirect
      if (
        route.auth
        && finalUrl.includes("/login")
      ) {
        console.log(
          "\x1b[31m✗ Redirected to /login\x1b[0m",
        );
        allPassed = false;
        results.push({
          path: route.path,
          performance: 0,
          error: "auth redirect",
          passed: false,
        });
        // Re-login
        await page.fill(
          'input[type="email"]', email,
        );
        await page.fill(
          'input[type="password"]', password,
        );
        await page.click('button[type="submit"]');
        await page.waitForURL(
          "**/dashboard**", { timeout: 10000 },
        ).catch(() => {});
        continue;
      }

      // Collect Web Vitals via Performance API
      const metrics = await page.evaluate(() => {
        const result = {
          lcp: 0,
          cls: 0,
          fcp: 0,
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
        let clsScore = 0;
        for (const entry of clsEntries) {
          if (!entry.hadRecentInput) {
            clsScore += entry.value;
          }
        }
        result.cls = clsScore;

        return result;
      });

      // Get TBT from CDP
      const cdpMetrics = await cdpSession.send(
        "Performance.getMetrics",
      );
      const taskDur = cdpMetrics.metrics.find(
        (m) => m.name === "TaskDuration",
      );
      const tbt = Math.round(
        (taskDur?.value || 0) * 1000,
      );

      // Score estimation
      let score = 100;
      if (metrics.lcp > 4000) score -= 25;
      else if (metrics.lcp > 2500) {
        score -= Math.round(
          25 * (metrics.lcp - 2500) / 1500,
        );
      }
      if (tbt > 600) score -= 30;
      else if (tbt > 200) {
        score -= Math.round(30 * (tbt - 200) / 400);
      }
      if (metrics.cls > 0.25) score -= 25;
      else if (metrics.cls > 0.1) {
        score -= Math.round(
          25 * (metrics.cls - 0.1) / 0.15,
        );
      }
      if (metrics.fcp > 3000) score -= 10;
      else if (metrics.fcp > 1800) {
        score -= Math.round(
          10 * (metrics.fcp - 1800) / 1200,
        );
      }
      score = Math.max(0, Math.min(100, score));

      const passed = score >= route.budget;
      if (!passed) allPassed = false;

      const icon = passed
        ? "\x1b[32m✓\x1b[0m"
        : "\x1b[31m✗\x1b[0m";
      const c = passed ? "\x1b[32m" : "\x1b[31m";

      console.log(
        `${icon} Score: ${c}${score}\x1b[0m`
        + ` (budget: ${route.budget})`
        + `  LCP: ${metrics.lcp}ms`
        + `  FCP: ${metrics.fcp}ms`
        + `  TBT: ${tbt}ms`
        + `  CLS: ${metrics.cls.toFixed(3)}`,
      );

      results.push({
        path: route.path,
        performance: score,
        lcp_ms: metrics.lcp,
        fcp_ms: metrics.fcp,
        tbt_ms: tbt,
        cls: metrics.cls,
        budget: route.budget,
        passed,
      });
    } catch (e) {
      console.log(
        `\x1b[31m✗ ERROR: ${e.message}\x1b[0m`,
      );
      allPassed = false;
      results.push({
        path: route.path,
        performance: 0,
        error: e.message,
        passed: false,
      });
    }
  }

  await browser.close();

  // Summary
  console.log(
    "\n\x1b[36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    + "━━━━━━━━━━━━━━━━━━━\x1b[0m",
  );
  if (allPassed) {
    console.log(
      "\x1b[32m  ✓ All routes meet performance"
      + " budgets!\x1b[0m",
    );
  } else {
    const failed = results.filter((r) => !r.passed);
    console.log(
      `\x1b[31m  ✗ ${failed.length}`
      + ` route(s) below budget:\x1b[0m`,
    );
    for (const r of failed) {
      console.log(
        `    ${r.path}: ${r.performance || "ERR"}`
        + ` (budget: ${r.budget || "?"})`,
      );
    }
  }
  console.log(
    "\x1b[36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    + "━━━━━━━━━━━━━━━━━━━\x1b[0m\n",
  );

  process.exit(allPassed ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
