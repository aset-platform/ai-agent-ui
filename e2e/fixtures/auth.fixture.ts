/**
 * Auth fixtures for Playwright tests.
 *
 * Provides ``userToken`` and ``adminToken`` fixtures that
 * return a valid JWT for the general user and superuser
 * respectively.  Dashboard tests use these to append
 * ``?token=`` to Dash URLs.
 *
 * Tokens are read from the storageState JSON files produced
 * by the setup project — NO extra ``/auth/login`` API calls.
 */

import fs from "fs";
import path from "path";

import { test as base } from "@playwright/test";

const AUTH_DIR = path.join(__dirname, "..", ".auth");

/**
 * Read a cached JWT from a storageState JSON file.
 *
 * The setup project writes localStorage entries with
 * ``auth_access_token`` — this function extracts that
 * value without making any API calls.
 */
function readCachedToken(filename: string): string {
  const filepath = path.join(AUTH_DIR, filename);
  const data = JSON.parse(fs.readFileSync(filepath, "utf8"));
  const origin = data.origins?.[0];
  const entry = origin?.localStorage?.find(
    (e: { name: string; value: string }) =>
      e.name === "auth_access_token",
  );
  if (!entry?.value) {
    throw new Error(
      `No auth_access_token in ${filename}`,
    );
  }
  return entry.value;
}

type AuthFixtures = {
  userToken: string;
  adminToken: string;
};

/**
 * Extended test with auth token fixtures.
 *
 * Usage::
 *
 *     import { test } from "../fixtures/auth.fixture";
 *     test("my test", async ({ page, userToken }) => {
 *       await page.goto(`/?token=${userToken}`);
 *     });
 */
export const test = base.extend<AuthFixtures>({
  userToken: async ({}, use) => {
    const token = readCachedToken("general-user.json");
    await use(token);
  },

  adminToken: async ({}, use) => {
    const token = readCachedToken("superuser.json");
    await use(token);
  },
});

export { expect } from "@playwright/test";
