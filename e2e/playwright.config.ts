import { defineConfig, devices } from "@playwright/test";

const FRONTEND_URL =
  process.env.FRONTEND_URL || "http://localhost:3000";
const BACKEND_URL =
  process.env.BACKEND_URL || "http://127.0.0.1:8181";

/**
 * Resource budget:
 *
 * Local: 1 worker (single Chromium). E2E runs alongside
 * 5 Docker services (backend, frontend, PG, Redis, docs)
 * + dev tools. One browser keeps CPU under control and
 * avoids starving backend APIs.
 *
 * CI: 2 workers — dedicated runner with no dev tools.
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: {
    timeout: 5_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
      threshold: 0.2,
      animations: "disabled",
    },
  },
  globalTimeout: 1_800_000,
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 2 : 1,
  /* Stop early locally — if services are down, no point
     burning CPU on 250 doomed tests. */
  maxFailures: process.env.CI ? 0 : 10,
  forbidOnly: !!process.env.CI,
  reporter: [["html", { open: "never" }], ["list"]],
  outputDir: process.env.CI
    ? "./test-results"
    : "/tmp/e2e-test-results",
  use: {
    headless: true,
    screenshot: "only-on-failure",
    /* Video recording adds ~30% CPU per browser.
       Only on CI where we need failure artifacts. */
    video: process.env.CI
      ? "retain-on-failure"
      : "off",
    trace: "on-first-retry",
    /* Reduce per-browser CPU overhead. */
    launchOptions: {
      args: [
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-extensions",
      ],
    },
  },

  projects: [
    /* ── Auth setup (runs first, produces storageState) ── */
    {
      name: "setup",
      testMatch: /.*\.setup\.ts/,
      testDir: "./setup",
    },

    /* ── Auth tests (no pre-auth, but waits for setup) ── */
    {
      name: "auth-chromium",
      testDir: "./tests/auth",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
      },
      dependencies: ["setup"],
    },

    /* ── Frontend tests (Next.js — auth, chat, profile) ── */
    {
      name: "frontend-chromium",
      testDir: "./tests/frontend",
      testIgnore: [
        /analytics.*\.spec\.ts/,
        /dashboard.*\.spec\.ts/,
        /insights.*\.spec\.ts/,
        /marketplace.*\.spec\.ts/,
        /portfolio.*\.spec\.ts/,
        /admin.*\.spec\.ts/,
        /theme-consistency.*\.spec\.ts/,
      ],
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
        storageState: ".auth/superuser.json",
      },
      dependencies: ["setup"],
    },

    /* ── Analytics tests (Next.js — all dashboard pages) ── */
    {
      name: "analytics-chromium",
      testDir: "./tests/frontend",
      testMatch: [
        /analytics.*\.spec\.ts/,
        /dashboard.*\.spec\.ts/,
        /insights.*\.spec\.ts/,
        /marketplace.*\.spec\.ts/,
        /portfolio.*\.spec\.ts/,
        /theme-consistency.*\.spec\.ts/,
      ],
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
        storageState: ".auth/general-user.json",
      },
      dependencies: ["setup"],
    },

    /* ── Admin tests (superuser role, Next.js /admin) ─── */
    {
      name: "admin-chromium",
      testDir: "./tests/frontend",
      testMatch: /admin.*\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
        storageState: ".auth/superuser.json",
      },
      dependencies: ["setup"],
    },

    /* ── Error handling tests ────────────────────────── */
    {
      name: "errors-chromium",
      testDir: "./tests/errors",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
        storageState: ".auth/general-user.json",
      },
      dependencies: ["setup"],
    },

    /* ── Performance tests (Lighthouse/Core Web Vitals) */
    {
      name: "performance",
      testDir: "./tests/performance",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
        storageState: ".auth/general-user.json",
      },
      dependencies: ["setup"],
    },
  ],

  webServer: {
    command:
      `cd ${process.env.PROJECT_ROOT || ".."} && ./run.sh start`,
    url: `${BACKEND_URL}/v1/agents`,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
    env: {
      ...process.env,
      AI_AGENT_UI_ENV: "test",
    },
  },
});
