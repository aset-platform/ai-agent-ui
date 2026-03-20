/**
 * Deep coverage E2E tests for the chat interface.
 *
 * Covers agent switching, input validation, message rendering,
 * and layout stability beyond the basic chat.spec.ts tests.
 */

import { test, expect } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";

test.describe("Chat deep coverage", () => {
  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
    await chatPage.goto();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });
  });

  test("all agent buttons are visible in selector", async () => {
    const buttons = chatPage.agentSelector.getByRole("button");
    const count = await buttons.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test("switching agent changes hint text", async ({ page }) => {
    // Get initial hint
    const initialHint = await page
      .locator("p.text-gray-400, p.text-gray-500")
      .first()
      .textContent();

    await chatPage.selectAgent("Stock Analysis");
    await page.waitForTimeout(500);

    const newHint = await page
      .locator("p.text-gray-400, p.text-gray-500")
      .first()
      .textContent();
    expect(newHint).not.toBe(initialHint);
  });

  test("empty input → send button disabled", async () => {
    await expect(chatPage.messageInput).toHaveValue("");
    await expect(chatPage.sendBtn).toBeDisabled();
  });

  test("Shift+Enter adds newline without sending", async ({
    page,
  }) => {
    await chatPage.messageInput.click();
    await chatPage.messageInput.pressSequentially("Line 1", {
      delay: 30,
    });
    await page.keyboard.down("Shift");
    await page.keyboard.press("Enter");
    await page.keyboard.up("Shift");
    await chatPage.messageInput.pressSequentially("Line 2", {
      delay: 30,
    });

    // Should still be in the textarea, not sent
    const value = await chatPage.messageInput.inputValue();
    expect(value).toContain("Line 1");
    expect(value).toContain("Line 2");
  });

  test("user message has distinct styling from assistant", async ({
    page,
  }) => {
    // Send via live backend (WS or HTTP)
    await chatPage.sendAndWaitForReply("I am the user.");

    const userBubble = chatPage.userMessages.last();
    const assistantBubble = chatPage.assistantMessages.last();

    await expect(userBubble).toBeVisible();
    await expect(assistantBubble).toBeVisible();

    // User and assistant rows should have different flex direction
    // (user is flex-row-reverse, assistant is flex-row)
    const userClass = await userBubble.getAttribute("class") ?? "";
    const assistantClass =
      await assistantBubble.getAttribute("class") ?? "";
    const userReversed = userClass.includes("flex-row-reverse");
    const assistantReversed =
      assistantClass.includes("flex-row-reverse");
    expect(userReversed).not.toBe(assistantReversed);
  });

  test("assistant response contains text", async () => {
    await chatPage.sendAndWaitForReply("Hello");

    const lastAssistant = chatPage.assistantMessages.last();
    const text = await lastAssistant.innerText();
    expect(text.length).toBeGreaterThan(0);
  });

  test("loading state shows status badge", async ({ page }) => {
    // Send a real message and quickly check for badge
    await chatPage.sendMessage("Tell me a joke");
    // Badge should appear during streaming
    await expect(chatPage.statusBadge).toBeVisible({
      timeout: 5_000,
    });
    await expect(chatPage.statusBadge).not.toBeEmpty();
  });

  test("long message does not break layout", async ({ page }) => {
    const longMsg = "A".repeat(500);
    await chatPage.messageInput.click();
    await chatPage.messageInput.fill(longMsg);
    await expect(chatPage.sendBtn).toBeEnabled({
      timeout: 5_000,
    });
    await chatPage.sendBtn.click();

    await expect(chatPage.userMessages.last()).toContainText(
      "AAAA",
      { timeout: 10_000 },
    );

    // Page should not have horizontal overflow
    const hasOverflow = await page.evaluate(() => {
      return (
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth
      );
    });
    expect(hasOverflow).toBe(false);
  });
});
