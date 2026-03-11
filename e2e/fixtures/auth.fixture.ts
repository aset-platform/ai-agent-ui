/**
 * Auth fixtures for Playwright tests.
 *
 * Provides ``userToken`` and ``adminToken`` fixtures that
 * return a valid JWT for the general user and superuser
 * respectively.  Dashboard tests use these to append
 * ``?token=`` to Dash URLs.
 */

import { test as base } from "@playwright/test";
import { apiLogin } from "../utils/api.helper";

const USER_EMAIL =
  process.env.TEST_USER_EMAIL || "test@demo.com";
const USER_PASSWORD =
  process.env.TEST_USER_PASSWORD || "Test1234!";
const ADMIN_EMAIL =
  process.env.TEST_ADMIN_EMAIL || "admin@demo.com";
const ADMIN_PASSWORD =
  process.env.TEST_ADMIN_PASSWORD || "Admin123!";

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
  userToken: async ({ request }, use) => {
    const { access_token } = await apiLogin(
      request,
      USER_EMAIL,
      USER_PASSWORD,
    );
    await use(access_token);
  },

  adminToken: async ({ request }, use) => {
    const { access_token } = await apiLogin(
      request,
      ADMIN_EMAIL,
      ADMIN_PASSWORD,
    );
    await use(access_token);
  },
});

export { expect } from "@playwright/test";
