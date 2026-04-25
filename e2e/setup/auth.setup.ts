/**
 * Auth setup project — produces storageState JSON files.
 *
 * Runs once before all dependent projects.  Logs in via API
 * and writes localStorage-based storageState files that the
 * frontend projects consume to skip the login page.
 */

import fs from "fs";
import path from "path";
import { URL } from "url";

import { test as setup, expect } from "@playwright/test";
import { type APIRequestContext } from "@playwright/test";

const BACKEND_HOST =
  process.env.BACKEND_URL || "http://127.0.0.1:8181";
const BACKEND = `${BACKEND_HOST}/v1`;
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

interface SetCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires: number;
  httpOnly: boolean;
  secure: boolean;
  sameSite: "Strict" | "Lax" | "None";
}

/**
 * Parse Set-Cookie response header(s) and return cookie objects
 * pointing at the FRONTEND origin so they survive Next.js's
 * /v1/* rewrite (proxy.ts edge gate reads them on the frontend
 * host, not the backend host).
 */
function parseSetCookieHeaders(
  raw: string | string[],
): SetCookie[] {
  const headers = Array.isArray(raw) ? raw : raw.split("\n");
  const frontendHost = new URL(FRONTEND).hostname;
  const out: SetCookie[] = [];
  for (const header of headers) {
    if (!header.trim()) continue;
    const parts = header.split(";").map((s) => s.trim());
    const [first, ...attrs] = parts;
    const eq = first.indexOf("=");
    if (eq < 0) continue;
    const name = first.slice(0, eq);
    const value = first.slice(eq + 1);
    let path = "/";
    let expires = Math.floor(Date.now() / 1000) + 3600;
    let httpOnly = false;
    let secure = false;
    let sameSite: "Strict" | "Lax" | "None" = "Lax";
    for (const a of attrs) {
      const lc = a.toLowerCase();
      if (lc === "httponly") httpOnly = true;
      else if (lc === "secure") secure = true;
      else if (lc.startsWith("path=")) path = a.slice(5);
      else if (lc.startsWith("max-age=")) {
        expires = Math.floor(Date.now() / 1000)
          + parseInt(a.slice(8), 10);
      } else if (lc.startsWith("samesite=")) {
        const v = a.slice(9).toLowerCase();
        sameSite =
          v === "strict" ? "Strict"
            : v === "none" ? "None" : "Lax";
      }
    }
    out.push({
      name,
      value,
      domain: frontendHost,
      path,
      expires,
      httpOnly,
      secure,
      sameSite,
    });
  }
  return out;
}

function writeStorageState(
  filename: string,
  token: string,
  cookies: SetCookie[],
): void {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(AUTH_DIR, filename),
    JSON.stringify({
      cookies,
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

/** Login with retry on 429/5xx; returns access token + the
 *  HttpOnly access_token / refresh_token cookies the proxy.ts
 *  edge gate now requires (added Sprint 8 phase A.2). */
async function loginWithRetry(
  request: APIRequestContext,
  email: string,
  password: string,
  label: string,
): Promise<{ token: string; cookies: SetCookie[] }> {
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = await request.post(
      `${BACKEND}/auth/login`,
      { data: { email, password } },
    );
    if (res.ok()) {
      const { access_token } = await res.json();
      const headers = res.headersArray()
        .filter((h) => h.name.toLowerCase() === "set-cookie")
        .map((h) => h.value);
      const cookies = parseSetCookieHeaders(headers);
      return { token: access_token, cookies };
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
  const { token, cookies } = await loginWithRetry(
    request,
    USER_EMAIL,
    USER_PASSWORD,
    "General user",
  );
  writeStorageState("general-user.json", token, cookies);
});

setup("authenticate superuser", async ({ request }) => {
  const { token, cookies } = await loginWithRetry(
    request,
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    "Superuser",
  );
  writeStorageState("superuser.json", token, cookies);
});
