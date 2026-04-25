#!/usr/bin/env node
/**
 * Lighthouse-all-routes auditor.
 *
 * Runs full Lighthouse audits (performance, accessibility,
 * best-practices, SEO) on every route — public + authenticated
 * — by launching Chromium via Playwright with a persistent user
 * data dir (so cookies/localStorage set by login survive the new
 * tab that Lighthouse opens over CDP).
 *
 * Usage:
 *   PERF_TEST_EMAIL=admin@demo.com \
 *   PERF_TEST_PASSWORD=Admin123! \
 *   node scripts/perf-lighthouse-all-routes.js
 *
 * Env:
 *   PERF_TEST_EMAIL     (default admin@demo.com)
 *   PERF_TEST_PASSWORD  (default Admin123!)
 *   PERF_BASE           (default http://localhost:3000)
 *   PERF_PORT           (CDP port, default 9222)
 */

const fs = require("fs");
const path = require("path");
const os = require("os");

const e2eDir = path.join(__dirname, "..", "e2e");
const frontendDir = path.join(__dirname, "..", "frontend");

// Lighthouse 12 is ESM-only; Playwright is CJS. Use dynamic
// import() so the same code path works in both cases (Node
// transparently interops CJS via `import()`). Resolution order:
// e2e → frontend (host-run), then bare module (perf container
// installs locally in /app/node_modules).
async function tryImport(candidates) {
  const errors = [];
  for (const p of candidates) {
    try {
      return await import(p);
    } catch (e) {
      errors.push(`${p}: ${e.code || e.message}`);
    }
  }
  throw new Error(
    `Could not import any of:\n  ${errors.join("\n  ")}`,
  );
}

const playwrightCandidates = [
  path.join(e2eDir, "node_modules", "playwright", "index.js"),
  path.join(frontendDir, "node_modules", "playwright", "index.js"),
  "playwright",
];
const lighthouseCandidates = [
  path.join(
    frontendDir, "node_modules", "lighthouse", "core", "index.js",
  ),
  "lighthouse",
];

const BASE = process.env.PERF_BASE || "http://localhost:3000";
const CDP_PORT = parseInt(
  process.env.PERF_PORT || "9222", 10,
);
const EMAIL =
  process.env.PERF_TEST_EMAIL || "admin@demo.com";
const PASSWORD =
  process.env.PERF_TEST_PASSWORD || "Admin123!";

// 9 base + 25 tabbed = 34 audit points. `/analytics/marketplace`
// is dropped — redirects to `/analytics` as of Sprint 7.
const ANALYSIS_TABS = [
  "portfolio",
  "portfolio-forecast",
  "analysis",
  "forecast",
  "compare",
  "recommendations",
];
const INSIGHTS_TABS = [
  "screener",
  "risk",
  "sectors",
  "targets",
  "dividends",
  "correlation",
  "quarterly",
  "piotroski",
  "screenql",
];
const ADMIN_TABS = [
  "users",
  "audit",
  "observability",
  "transactions",
  "scheduler",
  "recommendations",
  "maintenance",
  "my_account",
  "my_audit",
  "my_llm",
];

const ROUTES = [
  { path: "/login", auth: false },
  { path: "/dashboard", auth: true },
  { path: "/analytics", auth: true },
  { path: "/analytics/analysis", auth: true },
  { path: "/analytics/compare", auth: true },
  { path: "/analytics/insights", auth: true },
  { path: "/admin", auth: true },
  { path: "/docs", auth: true },
  { path: "/insights", auth: true },
  ...ANALYSIS_TABS.map((t) => ({
    path: `/analytics/analysis?tab=${t}`,
    auth: true,
  })),
  ...INSIGHTS_TABS.map((t) => ({
    path: `/analytics/insights?tab=${t}`,
    auth: true,
  })),
  ...ADMIN_TABS.map((t) => ({
    path: `/admin?tab=${t}`,
    auth: true,
  })),
];

const USER_DATA_DIR = fs.mkdtempSync(
  path.join(os.tmpdir(), "pw-lh-profile-"),
);

async function login(page) {
  await page.goto(`${BASE}/login`, {
    waitUntil: "networkidle",
    timeout: 30000,
  });
  const email = page
    .locator('input[type="email"], input[name="email"]')
    .first();
  const pwd = page
    .locator(
      'input[type="password"], input[name="password"]',
    )
    .first();
  // React onChange needs real keystrokes — `.fill()`
  // doesn't trigger validation on prod builds.
  await email.click();
  await email.pressSequentially(EMAIL, { delay: 15 });
  await pwd.click();
  await pwd.pressSequentially(PASSWORD, { delay: 15 });
  await page
    .waitForSelector(
      'button[type="submit"]:not([disabled])',
      { timeout: 10000 },
    )
    .catch(() => {});
  await page.click('button[type="submit"]');
  try {
    await page.waitForURL("**/dashboard**", {
      timeout: 30000,
    });
    return true;
  } catch (_e) {
    return !page.url().includes("/login");
  }
}

let chromium;
let lighthouse;

async function audit(url) {
  const result = await lighthouse(url, {
    port: CDP_PORT,
    output: "json",
    logLevel: "error",
    onlyCategories: [
      "performance",
      "accessibility",
      "best-practices",
      "seo",
    ],
    formFactor: "desktop",
    screenEmulation: {
      mobile: false,
      width: 1350,
      height: 940,
      deviceScaleFactor: 1,
      disabled: false,
    },
    throttlingMethod: "devtools",
  });
  return result.lhr;
}

function fmt(ms) {
  if (ms == null || Number.isNaN(ms)) return "—";
  return `${Math.round(ms)}`;
}

function pct(score) {
  if (score == null) return "—";
  return `${Math.round(score * 100)}`;
}

async function main() {
  // Resolve playwright + lighthouse before anything else so
  // failures surface with a clear message instead of a deep
  // Lighthouse stack trace.
  const pwModule = await tryImport(playwrightCandidates);
  chromium = pwModule.chromium ?? pwModule.default?.chromium;
  const lhModule = await tryImport(lighthouseCandidates);
  lighthouse = lhModule.default ?? lhModule;
  if (!chromium) {
    throw new Error("playwright module has no `chromium` export");
  }
  if (typeof lighthouse !== "function") {
    throw new Error(
      "lighthouse default export is not callable — got "
      + typeof lighthouse,
    );
  }

  console.log(
    `\n[*] Launching Chromium (user-data-dir=${USER_DATA_DIR})`,
  );
  const context = await chromium.launchPersistentContext(
    USER_DATA_DIR,
    {
      headless: true,
      args: [
        `--remote-debugging-port=${CDP_PORT}`,
        "--no-sandbox",
        "--disable-setuid-sandbox",
        // `crypto.randomUUID()` and other SubtleCrypto APIs are
        // gated on "secure context" (HTTPS, localhost, 127.0.0.1).
        // The perf container hits `http://frontend-perf:3000`
        // via docker DNS — neither. Without this flag, app JS
        // throws on first dashboard render → Lighthouse records
        // identical FCP=LCP across every authenticated route.
        `--unsafely-treat-insecure-origin-as-secure=${BASE}`,
      ],
    },
  );

  // Polyfill `crypto.randomUUID` — `http://frontend-perf:3000`
  // is not a "secure context" (HTTPS or localhost only), so
  // Chromium doesn't expose the API even with the
  // `--unsafely-treat-insecure-origin-as-secure` flag for this
  // specific method. App code throws on first use, killing
  // render. Polyfill at init-script time so every page in the
  // context gets a working implementation before any app JS runs.
  await context.addInitScript(() => {
    if (
      typeof window !== "undefined"
      && window.crypto
      && typeof window.crypto.randomUUID !== "function"
    ) {
      window.crypto.randomUUID = function randomUUID() {
        // RFC 4122 v4 implementation — not cryptographically
        // equivalent but sufficient for client-side keying.
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, (b) =>
          b.toString(16).padStart(2, "0"),
        ).join("");
        return (
          hex.slice(0, 8) + "-"
          + hex.slice(8, 12) + "-"
          + hex.slice(12, 16) + "-"
          + hex.slice(16, 20) + "-"
          + hex.slice(20)
        );
      };
    }
  });

  const page = context.pages()[0] || await context.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      console.log(`  [browser ERR] ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => {
    console.log(`  [page ERR] ${err.message}`);
  });
  page.on("requestfailed", (req) => {
    console.log(
      `  [req failed] ${req.method()} `
      + `${req.url()} — ${req.failure()?.errorText}`,
    );
  });
  console.log(`[*] Logging in as ${EMAIL}...`);
  const ok = await login(page);
  if (!ok) {
    const shot = path.join(
      __dirname, "..", ".lighthouseci",
      `login-fail-${Date.now()}.png`,
    );
    try {
      fs.mkdirSync(path.dirname(shot), { recursive: true });
      await page.screenshot({ path: shot, fullPage: true });
      console.error(
        `[!] Login failed — at ${page.url()}`,
      );
      console.error(`[i] Screenshot: ${shot}`);
      const title = await page.title().catch(() => "?");
      console.error(`[i] Page title: ${title}`);
    } catch (e) {
      console.error("[!] Screenshot failed:", e.message);
    }
    await context.close();
    process.exit(1);
  }
  console.log(`[+] Logged in (at ${page.url()})`);

  // Give React hydration a moment to write tokens.
  await page.waitForTimeout(1500);

  const outDir = path.join(
    __dirname, "..", ".lighthouseci",
  );
  fs.mkdirSync(outDir, { recursive: true });

  const results = [];
  let currentPage = page;
  // Lighthouse's CDP session leaks handles — after ~30 audits
  // on the same tab the next `Page.enable` call errors with
  // `Session closed`. Proactively rotate the target tab every
  // ROUTES_PER_PAGE audits so we never hit that ceiling.
  const ROUTES_PER_PAGE = 12;
  for (let i = 0; i < ROUTES.length; i++) {
    const route = ROUTES[i];
    if (i > 0 && i % ROUTES_PER_PAGE === 0) {
      console.log(
        `[*] Rotating target tab after ${i} audits ...`,
      );
      try {
        await currentPage.close();
      } catch (_e) {
        /* already closed */
      }
      currentPage = await context.newPage();
      // Re-navigate to an authed page so cookies + localStorage
      // warm up before the next Lighthouse audit opens its own
      // CDP tab. Without this, the next audit can race the
      // auth init.
      await currentPage
        .goto(`${BASE}/dashboard`, {
          waitUntil: "domcontentloaded",
          timeout: 15000,
        })
        .catch(() => {
          /* navigation errors are non-fatal — lighthouse
             opens its own tab anyway */
        });
      await currentPage.waitForTimeout(500);
    }
    const url = `${BASE}${route.path}`;
    process.stdout.write(
      `[*] Auditing ${route.path} ... `,
    );
    let lhr;
    try {
      lhr = await audit(url);
    } catch (err) {
      // Lighthouse's CDP session can die on any given route
      // (e.g. "Session closed. Most likely the page has been
      // closed"). Rotate the tab and retry ONCE before giving
      // up on the route. Without this, one bad route kills the
      // entire remaining batch.
      console.log(`FAILED (${err.message.slice(0, 60)}); rotating and retrying ...`);
      try { await currentPage.close(); } catch (_e) { /* noop */ }
      currentPage = await context.newPage();
      await currentPage
        .goto(`${BASE}/dashboard`, {
          waitUntil: "domcontentloaded",
          timeout: 15000,
        })
        .catch(() => { /* noop */ });
      await currentPage.waitForTimeout(500);
      try {
        lhr = await audit(url);
      } catch (err2) {
        console.log(`   RETRY FAILED: ${err2.message.slice(0, 80)}`);
        results.push({ route: route.path, error: err2.message });
        continue;
      }
    }
    try {
      const finalUrl = lhr.finalDisplayedUrl || lhr.finalUrl;
      // Strip querystring from the expected path before the
      // redirect sniff — tab variants (`?tab=foo`) often get
      // their query normalized away by Lighthouse/CDP.
      const expectedPath = route.path.split("?")[0];
      const redirected = !finalUrl.includes(expectedPath);
      const safe = route.path
        .replace(/\?/g, "--")
        .replace(/=/g, "-")
        .replace(/&/g, "_")
        .replace(/\//g, "_")
        .replace(/^_+/, "") || "root";
      const filename = `pw-${safe}.json`;
      fs.writeFileSync(
        path.join(outDir, filename),
        JSON.stringify(lhr, null, 2),
      );
      const r = {
        route: route.path,
        perf: pct(lhr.categories.performance?.score),
        a11y: pct(lhr.categories.accessibility?.score),
        bp: pct(lhr.categories["best-practices"]?.score),
        seo: pct(lhr.categories.seo?.score),
        fcp_ms: fmt(
          lhr.audits["first-contentful-paint"]
            ?.numericValue,
        ),
        lcp_ms: fmt(
          lhr.audits["largest-contentful-paint"]
            ?.numericValue,
        ),
        tbt_ms: fmt(
          lhr.audits["total-blocking-time"]
            ?.numericValue,
        ),
        cls: (
          lhr.audits["cumulative-layout-shift"]
            ?.numericValue ?? 0
        ).toFixed(3),
        redirected: redirected ? finalUrl : "",
      };
      results.push(r);
      console.log(
        `perf=${r.perf} a11y=${r.a11y} `
        + `lcp=${r.lcp_ms}ms`
        + (redirected
          ? ` (REDIRECTED → ${finalUrl})`
          : ""),
      );
    } catch (e) {
      console.log(`FAILED: ${e.message}`);
      results.push({
        route: route.path,
        error: e.message,
      });
    }
  }

  await context.close();
  try {
    fs.rmSync(USER_DATA_DIR, {
      recursive: true,
      force: true,
    });
  } catch (_e) {
    /* ignore */
  }

  console.log("\n===== SUMMARY =====\n");
  console.table(results);

  const summaryFile = path.join(
    outDir, "pw-lh-summary.json",
  );
  fs.writeFileSync(
    summaryFile,
    JSON.stringify(
      { base: BASE, email: EMAIL, results }, null, 2,
    ),
  );
  console.log(`\n[i] Summary: ${summaryFile}`);
  console.log(
    `[i] Per-route LHR JSONs: ${outDir}/pw-*.json`,
  );
}

// Lighthouse detaches several CDP promises that can reject
// AFTER their parent `await lighthouse()` has returned. Node
// treats these as `unhandledRejection`; the default handler
// terminates the process at the next tick, crashing the loop
// mid-run. Downgrade to a warning so the loop's own retry
// logic can handle the route.
process.on("unhandledRejection", (reason) => {
  const msg = reason instanceof Error
    ? reason.message
    : String(reason);
  console.warn(
    `  [unhandledRejection swallowed] ${msg.slice(0, 120)}`,
  );
});
process.on("uncaughtException", (err) => {
  console.warn(
    `  [uncaughtException swallowed] ${err.message.slice(0, 120)}`,
  );
});

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
