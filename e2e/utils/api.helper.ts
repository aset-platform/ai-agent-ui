/**
 * Direct backend API helpers for test setup and teardown.
 *
 * These bypass the UI to create preconditions (users, tickers)
 * and clean up after tests.
 */

import { type APIRequestContext } from "@playwright/test";

const BACKEND =
  process.env.BACKEND_URL || "http://127.0.0.1:8181";

interface LoginResult {
  access_token: string;
  refresh_token: string;
}

/** Log in via API and return the JWT pair (retries on 5xx). */
export async function apiLogin(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<LoginResult> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await request.post(
      `${BACKEND}/auth/login`,
      { data: { email, password } },
    );
    if (res.ok()) return res.json();
    if (res.status() >= 500 && attempt < 2) {
      await new Promise((r) => setTimeout(r, 1_000));
      continue;
    }
    throw new Error(
      `API login failed (${res.status()}): ${await res.text()}`,
    );
  }
  throw new Error("apiLogin: unreachable");
}

/** Link a ticker to the authenticated user. */
export async function apiLinkTicker(
  request: APIRequestContext,
  token: string,
  ticker: string,
): Promise<void> {
  await request.post(`${BACKEND}/users/me/tickers`, {
    data: { ticker, source: "e2e-test" },
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** Unlink a ticker from the authenticated user. */
export async function apiUnlinkTicker(
  request: APIRequestContext,
  token: string,
  ticker: string,
): Promise<void> {
  await request.delete(
    `${BACKEND}/users/me/tickers/${ticker}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
}

/** Fetch the current user profile. */
export async function apiGetProfile(
  request: APIRequestContext,
  token: string,
): Promise<Record<string, unknown>> {
  const res = await request.get(`${BACKEND}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.json();
}
