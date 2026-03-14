import { defineConfig, devices } from "@playwright/test";

const FRONTEND_URL =
  process.env.FRONTEND_URL || "http://localhost:3000";
const DASHBOARD_URL =
  process.env.DASHBOARD_URL || "http://127.0.0.1:8050";
const BACKEND_URL =
  process.env.BACKEND_URL || "http://127.0.0.1:8181";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  globalTimeout: 1_800_000,
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 3 : 3,
  forbidOnly: !!process.env.CI,
  reporter: [["html", { open: "never" }], ["list"]],
  /* Store test artifacts outside the project tree so the Dash
     debug reloader (which watches all files) is not triggered. */
  outputDir: process.env.CI
    ? "./test-results"
    : "/tmp/e2e-test-results",
  use: {
    headless: true,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    video: "retain-on-failure",
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

    /* ── Frontend tests (Next.js on port 3000) ─────────── */
    {
      name: "frontend-chromium",
      testDir: "./tests/frontend",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND_URL,
        storageState: ".auth/general-user.json",
      },
      dependencies: ["setup"],
    },

    /* ── Dashboard tests (Dash on port 8050) ───────────── */
    {
      name: "dashboard-chromium",
      testDir: "./tests/dashboard",
      testIgnore: /admin.*\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: DASHBOARD_URL,
      },
      dependencies: ["setup"],
    },

    /* ── Admin tests (superuser role) ──────────────────── */
    {
      name: "admin-chromium",
      testDir: "./tests/dashboard",
      testMatch: /admin.*\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: DASHBOARD_URL,
      },
      dependencies: ["setup"],
    },

    /* ── Error handling tests ──────────────────────────── */
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
  ],

  webServer: {
    command: `cd ${process.env.PROJECT_ROOT || ".."} && ./run.sh start`,
    url: `${BACKEND_URL}/v1/agents`,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
  },
});
