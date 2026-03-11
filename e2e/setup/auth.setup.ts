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
import { type APIRequestContext } from "@playwright/test";

const BACKEND =
  process.env.BACKEND_URL || "http://127.0.0.1:8181";
const FRONTEND =
  process.env.FRONTEND_URL || "http://localhost:3000";
const AUTH_DIR = path.join(__dirname, "..", ".auth");

const USER_EMAIL =
  process.env.TEST_USER_EMAIL || "test@demo.com";
const USER_PASSWORD =
  process.env.TEST_USER_PASSWORD || "Test1234!";
const ADMIN_EMAIL =
  process.env.TEST_ADMIN_EMAIL || "admin@demo.com";
const ADMIN_PASSWORD =
  process.env.TEST_ADMIN_PASSWORD || "Admin123!";

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

/** Login with retry on 429/5xx. */
async function loginWithRetry(
  request: APIRequestContext,
  email: string,
  password: string,
  label: string,
): Promise<string> {
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = await request.post(
      `${BACKEND}/auth/login`,
      { data: { email, password } },
    );
    if (res.ok()) {
      const { access_token } = await res.json();
      return access_token;
    }
    if (
      (res.status() === 429 || res.status() >= 500) &&
      attempt < 4
    ) {
      const wait = res.status() === 429 ? 3_000 : 1_000;
      await new Promise((r) => setTimeout(r, wait));
      continue;
    }
    const body = await res.text();
    throw new Error(
      `${label} login failed (${res.status()}): ${body}`,
    );
  }
  throw new Error(`${label} login failed after 5 attempts`);
}

setup("authenticate general user", async ({ request }) => {
  const token = await loginWithRetry(
    request,
    USER_EMAIL,
    USER_PASSWORD,
    "General user",
  );
  writeStorageState("general-user.json", token);
});

setup("authenticate superuser", async ({ request }) => {
  const token = await loginWithRetry(
    request,
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    "Superuser",
  );
  writeStorageState("superuser.json", token);
});
