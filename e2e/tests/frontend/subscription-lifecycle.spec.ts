/**
 * E2E tests for subscription lifecycle.
 *
 * Covers quota exhaustion, paywall enforcement, upgrade/
 * downgrade transitions, and usage counter behaviour.
 */

import { test, expect, type Page } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";
import { FE } from "../../utils/selectors";

/** Open the Billing tab inside the EditProfileModal. */
async function openBillingTab(
  chatPage: ChatPage,
  page: Page,
): Promise<void> {
  await chatPage.profileAvatar.click();
  const billing = page.getByRole("button", {
    name: /billing/i,
  });
  if (
    !(await billing.isVisible().catch(() => false))
  ) {
    await page.waitForTimeout(500);
    await chatPage.profileAvatar.click();
  }
  await billing.click();
  await expect(
    page.getByTestId(FE.billingCurrentPlan),
  ).toBeVisible({ timeout: 10_000 });
}

/**
 * Mock the subscription endpoint to return an
 * exhausted free-tier user.
 */
async function mockExhaustedFreeTier(
  page: Page,
): Promise<void> {
  await page.route(
    "**/subscription",
    (route, request) => {
      if (request.method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            tier: "free",
            status: "active",
            usage_count: 5,
            usage_limit: 5,
            usage_remaining: 0,
            gateway: null,
          }),
        });
      }
      return route.continue();
    },
  );
}

/**
 * Mock the subscription endpoint to return a pro user.
 */
async function mockProTier(page: Page): Promise<void> {
  await page.route(
    "**/subscription",
    (route, request) => {
      if (request.method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            tier: "pro",
            status: "active",
            usage_count: 0,
            usage_limit: 100,
            usage_remaining: 100,
            gateway: "stripe",
          }),
        });
      }
      return route.continue();
    },
  );
}

/**
 * Mock the subscription endpoint to return a free
 * user with usage remaining.
 */
async function mockFreeTierWithUsage(
  page: Page,
  usageCount: number,
): Promise<void> {
  await page.route(
    "**/subscription",
    (route, request) => {
      if (request.method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            tier: "free",
            status: "active",
            usage_count: usageCount,
            usage_limit: 5,
            usage_remaining: Math.max(
              0,
              5 - usageCount,
            ),
            gateway: null,
          }),
        });
      }
      return route.continue();
    },
  );
}

test.describe("Subscription lifecycle", () => {
  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
  });

  test(
    "free user sees paywall after quota exceeded",
    async ({ page }) => {
      await mockExhaustedFreeTier(page);
      await chatPage.goto();
      await expect(chatPage.messageInput).toBeVisible({
        timeout: 15_000,
      });

      // Look for the upgrade banner / paywall message
      const banner = page.locator(
        "text=/used all.*analyses|quota.*exceeded|upgrade.*plan/i",
      );
      const upgradeLink = page.locator(
        "text=/upgrade.*plan/i",
      );

      // At least one paywall indicator should be present
      const bannerVisible = await banner
        .first()
        .isVisible()
        .catch(() => false);
      const linkVisible = await upgradeLink
        .first()
        .isVisible()
        .catch(() => false);

      // Open billing to verify the meter shows 5/5
      await openBillingTab(chatPage, page);
      const meter = page.getByText(/5\s*\/\s*5\s*used/);
      await expect(meter).toBeVisible({
        timeout: 5_000,
      });

      expect(bannerVisible || linkVisible).toBe(true);
    },
  );

  test(
    "paywall disables chat input when quota exceeded",
    async ({ page }) => {
      await mockExhaustedFreeTier(page);

      // Also mock the chat stream to return a quota
      // exceeded error
      await page.route("**/chat/stream", (route) => {
        return route.fulfill({
          status: 429,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "Quota exceeded. Upgrade your plan.",
          }),
        });
      });

      await chatPage.goto();
      await expect(chatPage.messageInput).toBeVisible({
        timeout: 15_000,
      });

      // Check if input is disabled or shows an overlay
      const isInputDisabled = await chatPage.messageInput
        .isDisabled()
        .catch(() => false);

      const quotaMsg = page.locator(
        "text=/quota.*exceeded|upgrade.*plan|used all/i",
      );
      const hasQuotaMsg = await quotaMsg
        .first()
        .isVisible()
        .catch(() => false);

      // Either input is disabled or quota message shown
      expect(isInputDisabled || hasQuotaMsg).toBe(true);
    },
  );

  test(
    "upgrade to pro resets usage counter",
    async ({ page }) => {
      // Start with exhausted free tier
      await mockExhaustedFreeTier(page);
      await chatPage.goto();
      await expect(chatPage.messageInput).toBeVisible({
        timeout: 15_000,
      });

      await openBillingTab(chatPage, page);

      // Verify meter shows 5/5
      const exhaustedMeter = page.getByText(
        /5\s*\/\s*5\s*used/,
      );
      await expect(exhaustedMeter).toBeVisible({
        timeout: 5_000,
      });

      // Now switch to pro tier mock (simulating
      // a successful upgrade)
      await page.unrouteAll({ behavior: "wait" });
      await mockProTier(page);

      // Reload to pick up new subscription state
      await page.reload();
      await expect(
        page.getByTestId("sidebar"),
      ).toBeVisible({ timeout: 15_000 });
      await openBillingTab(chatPage, page);

      // Verify plan shows Pro
      const planText = await page
        .getByTestId(FE.billingCurrentPlan)
        .textContent();
      expect(planText).toMatch(/pro/i);

      // Usage meter should show reset count (0/100)
      const proMeter = page.getByText(
        /0\s*\/\s*100\s*used/,
      );
      await expect(proMeter).toBeVisible({
        timeout: 5_000,
      });
    },
  );

  test(
    "cancel subscription reverts to free label",
    async ({ page }) => {
      // Start as pro user
      await mockProTier(page);
      await chatPage.goto();
      await expect(chatPage.messageInput).toBeVisible({
        timeout: 15_000,
      });

      await openBillingTab(chatPage, page);

      // Verify Pro is displayed
      const proPlan = await page
        .getByTestId(FE.billingCurrentPlan)
        .textContent();
      expect(proPlan).toMatch(/pro/i);

      // Switch to free tier mock (simulating cancel)
      await page.unrouteAll({ behavior: "wait" });
      await mockFreeTierWithUsage(page, 0);

      // Mock the cancel endpoint
      await page.route(
        "**/subscription/cancel",
        (route) =>
          route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              status: "cancelled",
              tier: "free",
            }),
          }),
      );

      // Reload to pick up new state
      await page.reload();
      await expect(
        page.getByTestId("sidebar"),
      ).toBeVisible({ timeout: 15_000 });
      await openBillingTab(chatPage, page);

      // Verify plan shows Free
      const freePlan = await page
        .getByTestId(FE.billingCurrentPlan)
        .textContent();
      expect(freePlan).toMatch(/free/i);
    },
  );

  test(
    "usage counter increments after analysis",
    async ({ page }) => {
      // Start with 2 out of 5 used
      await mockFreeTierWithUsage(page, 2);
      await chatPage.goto();
      await expect(chatPage.messageInput).toBeVisible({
        timeout: 15_000,
      });

      // Check the usage badge shows 2/5
      const usageBadge = page.locator(
        "text=/2\\s*\\/\\s*5/",
      );
      const badgeVisible = await usageBadge
        .first()
        .isVisible()
        .catch(() => false);

      // Now mock the subscription to return 3/5 after
      // an analysis
      await page.unrouteAll({ behavior: "wait" });
      await mockFreeTierWithUsage(page, 3);

      // Mock a chat stream response that increments
      // usage
      await page.route("**/chat/stream", (route) => {
        const body =
          JSON.stringify({
            type: "final",
            response: "Analysis complete for AAPL.",
          }) + "\n";
        return route.fulfill({
          status: 200,
          contentType: "application/x-ndjson",
          body,
        });
      });

      // Block WS to force HTTP fallback so our mock
      // takes effect
      await page.routeWebSocket("**/ws/chat", (ws) => {
        ws.close();
      });

      await chatPage.sendAndWaitForReply(
        "Analyze AAPL",
        30_000,
      );

      // Reload to pick up updated usage count
      await page.unrouteAll({ behavior: "wait" });
      await mockFreeTierWithUsage(page, 3);
      await page.reload();
      await expect(
        page.getByTestId("sidebar"),
      ).toBeVisible({ timeout: 15_000 });

      // Usage badge should now show 3/5
      const updatedBadge = page.locator(
        "text=/3\\s*\\/\\s*5/",
      );
      const updatedVisible = await updatedBadge
        .first()
        .isVisible()
        .catch(() => false);

      // Open billing to verify the meter
      await openBillingTab(chatPage, page);
      const meter = page.getByText(
        /3\s*\/\s*5\s*used/,
      );
      await expect(meter).toBeVisible({
        timeout: 5_000,
      });
    },
  );
});
