/**
 * Auth setup project — produces storageState JSON files.
 *
 * Runs once before all dependent projects.  Logs in via API
 * and writes localStorage-based storageState files that the
 * frontend projects consume to skip the login page.
 */

import fs from "fs";
import path from "path";

import { test as setup, expect } from "@playwright/test";

const BACKEND =
  process.env.BACKEND_URL || "http://127.0.0.1:8181";
const FRONTEND =
  process.env.FRONTEND_URL || "http://localhost:3000";
const AUTH_DIR = path.join(__dirname, "..", ".auth");

const USER_EMAIL =
  process.env.TEST_USER_EMAIL || "test@example.com";
const USER_PASSWORD =
  process.env.TEST_USER_PASSWORD || "TestPassword123!";
const ADMIN_EMAIL =
  process.env.TEST_ADMIN_EMAIL || "admin@example.com";
const ADMIN_PASSWORD =
  process.env.TEST_ADMIN_PASSWORD || "AdminPassword123!";

function writeStorageState(
  filename: string,
  token: string,
): void {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(AUTH_DIR, filename),
    JSON.stringify({
      cookies: [],
      origins: [
        {
          origin: FRONTEND,
          localStorage: [
            { name: "auth_access_token", value: token },
          ],
        },
      ],
    }),
  );
}

setup("authenticate general user", async ({ request }) => {
  const res = await request.post(`${BACKEND}/auth/login`, {
    data: { email: USER_EMAIL, password: USER_PASSWORD },
  });
  if (!res.ok()) {
    const body = await res.text();
    throw new Error(
      `General user login failed (${res.status()}): ${body}`,
    );
  }
  const { access_token } = await res.json();
  writeStorageState("general-user.json", access_token);
});

setup("authenticate superuser", async ({ request }) => {
  const res = await request.post(`${BACKEND}/auth/login`, {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  });
  if (!res.ok()) {
    const body = await res.text();
    throw new Error(
      `Superuser login failed (${res.status()}): ${body}`,
    );
  }
  const { access_token } = await res.json();
  writeStorageState("superuser.json", access_token);
});
