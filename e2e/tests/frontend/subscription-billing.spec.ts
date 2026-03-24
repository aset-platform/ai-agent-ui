/**
 * E2E tests for subscription billing UI.
 *
 * Tests the Billing tab in the EditProfileModal:
 * pricing cards, current plan, usage meter, gateway selector.
 */

import { test, expect, type Page } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";

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
    page.getByText("CURRENT PLAN"),
  ).toBeVisible({ timeout: 10_000 });
}

test.describe("Billing tab", () => {
  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
    await chatPage.goto();
    await expect(
      chatPage.messageInput,
    ).toBeVisible({ timeout: 15_000 });
  });

  test("billing tab loads and shows current plan", async ({
    page,
  }) => {
    await openBillingTab(chatPage, page);
    await expect(
      page.getByText("CURRENT PLAN"),
    ).toBeVisible();
    // Should show one of: Free, Pro, Premium
    const planText = await page
      .locator("text=CURRENT PLAN")
      .locator("..")
      .textContent();
    expect(planText).toMatch(/Free|Pro|Premium/);
  });

  test("pricing cards show all 3 tiers", async ({
    page,
  }) => {
    await openBillingTab(chatPage, page);
    await expect(
      page.getByText("Free forever"),
    ).toBeVisible();
    // Pro and Premium cards visible
    const proCard = page.locator(
      "text=/499\\/mo|\\$6\\/mo/",
    );
    const premCard = page.locator(
      "text=/1,499\\/mo|\\$18\\/mo/",
    );
    await expect(proCard.first()).toBeVisible();
    await expect(premCard.first()).toBeVisible();
  });

  test("usage meter shows count", async ({
    page,
  }) => {
    await openBillingTab(chatPage, page);
    const meter = page.getByText(/\d+\s*\/\s*\d+\s*used/);
    await expect(meter).toBeVisible({
      timeout: 5_000,
    });
  });

  test("gateway selector toggles INR/USD prices", async ({
    page,
  }) => {
    await openBillingTab(chatPage, page);

    // Default should be INR (Razorpay)
    const inrBtn = page.getByRole("button", {
      name: /UPI.*INR/i,
    });
    const usdBtn = page.getByRole("button", {
      name: /International.*USD/i,
    });
    await expect(inrBtn).toBeVisible();
    await expect(usdBtn).toBeVisible();

    // Click USD
    await usdBtn.click();
    await expect(
      page.getByText("$6/mo"),
    ).toBeVisible();
    await expect(
      page.getByText("$18/mo"),
    ).toBeVisible();

    // Click INR
    await inrBtn.click();
    await expect(
      page.locator("text=/₹499\\/mo/"),
    ).toBeVisible();
  });

  test("current plan card has 'Current plan' label", async ({
    page,
  }) => {
    await openBillingTab(chatPage, page);
    await expect(
      page.getByText("Current plan"),
    ).toBeVisible();
  });
});
