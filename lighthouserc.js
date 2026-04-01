/**
 * Lighthouse CI configuration.
 *
 * Usage:
 *   cd frontend && npm run perf:check
 *
 * Auth: Puppeteer script logs in via backend API, stores
 * token as a cookie, and injects a CDP script that copies
 * the cookie to localStorage on every page load.
 *
 * Requires backend running on port 8181.
 * Set PERF_TEST_EMAIL and PERF_TEST_PASSWORD env vars.
 */
module.exports = {
  ci: {
    collect: {
      startServerCommand:
        "cd frontend && npm run build"
        + " && npx next start -p 3030",
      startServerReadyPattern: "Ready in",
      startServerReadyTimeout: 60000,
      chromePath:
        process.env.CHROME_PATH
        || "/Applications/Google Chrome.app"
        + "/Contents/MacOS/Google Chrome",
      numberOfRuns: 1,
      puppeteerScript: "./scripts/lighthouse-auth.js",
      puppeteerLaunchOptions: {
        headless: true,
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
        ],
      },
      url: [
        // Public
        "http://localhost:3030/login",
        // Authenticated
        "http://localhost:3030/dashboard",
        "http://localhost:3030/analytics",
        "http://localhost:3030/analytics/analysis",
        "http://localhost:3030/analytics/compare",
        "http://localhost:3030/analytics/insights",
        "http://localhost:3030/analytics/marketplace",
        "http://localhost:3030/admin",
        "http://localhost:3030/docs",
        "http://localhost:3030/insights",
      ],
      settings: {
        preset: "desktop",
      },
    },

    assert: {
      assertions: {
        // Global minimums
        "categories:performance": [
          "error",
          { minScore: 0.7 },
        ],
        "categories:accessibility": [
          "warn",
          { minScore: 0.9 },
        ],
        "categories:best-practices": [
          "warn",
          { minScore: 0.9 },
        ],
        "categories:seo": [
          "warn",
          { minScore: 0.9 },
        ],
        // Core Web Vitals
        "largest-contentful-paint": [
          "error",
          { maxNumericValue: 4000 },
        ],
        "cumulative-layout-shift": [
          "error",
          { maxNumericValue: 0.15 },
        ],
        "total-blocking-time": [
          "warn",
          { maxNumericValue: 600 },
        ],
        "first-contentful-paint": [
          "warn",
          { maxNumericValue: 3000 },
        ],
      },
    },

    upload: {
      target: "temporary-public-storage",
    },
  },
};
