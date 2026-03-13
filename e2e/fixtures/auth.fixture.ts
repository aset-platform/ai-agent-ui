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

import { test as base } from "@playwright/test";

import { readCachedToken } from "../utils/auth.helper";

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
