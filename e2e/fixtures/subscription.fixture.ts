/**
 * Subscription fixture for E2E tests.
 *
 * Provides helpers to reset users to the free tier and
 * exhaust their usage quota for paywall testing.
 */

import { request as pwRequest } from "@playwright/test";
import { test as authTest } from "./auth.fixture";
import { getSubscription } from "../utils/subscription.helper";

const BACKEND =
  `${process.env.BACKEND_URL || "http://127.0.0.1:8181"}/v1`;

type SubscriptionFixtures = {
  resetToFreeTier: (token: string) => Promise<void>;
  exhaustQuota: (token: string) => Promise<void>;
};

/**
 * Extended test with subscription helpers.
 *
 * Usage::
 *
 *     import { test } from "../fixtures/subscription.fixture";
 *     test("paywall shows", async ({ page, resetToFreeTier, userToken }) => {
 *       await resetToFreeTier(userToken);
 *     });
 */
export const test = authTest.extend<SubscriptionFixtures>({
  resetToFreeTier: async ({}, use) => {
    const fn = async (token: string): Promise<void> => {
      const ctx = await pwRequest.newContext();
      try {
        await ctx.post(
          `${BACKEND}/subscription/cancel`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          },
        );
      } catch {
        // Already on free tier — ignore.
      }
      await ctx.dispose();
    };
    await use(fn);
  },

  exhaustQuota: async ({}, use) => {
    const fn = async (token: string): Promise<void> => {
      const ctx = await pwRequest.newContext();
      // Read current subscription to find remaining quota
      const sub = await getSubscription(ctx, token);
      const remaining = sub.usage_remaining ?? 0;
      // Burn through remaining quota by calling
      // the usage-increment endpoint repeatedly
      for (let i = 0; i < remaining + 1; i++) {
        await ctx.post(
          `${BACKEND}/subscription/usage/increment`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          },
        );
      }
      await ctx.dispose();
    };
    await use(fn);
  },
});

export { expect } from "@playwright/test";
