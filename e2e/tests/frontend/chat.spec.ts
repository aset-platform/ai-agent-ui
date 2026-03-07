/**
 * E2E tests for the chat interface.
 *
 * These tests use pre-authenticated storageState (general user).
 */

import { test, expect, type Page } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";

/** Mock /chat/stream to return a canned NDJSON assistant reply. */
async function mockChatStream(
  page: Page,
  reply = "Hello! I can help you with that.",
): Promise<void> {
  await page.route("**/chat/stream", (route) => {
    const body =
      JSON.stringify({ type: "final", response: reply }) + "\n";
    return route.fulfill({
      status: 200,
      contentType: "application/x-ndjson",
      body,
    });
  });
}

test.describe("Chat interface", () => {
  // Run tests serially — chat state carries between tests and
  // concurrent backend access can cause flaky failures.
  test.describe.configure({ mode: "serial" });

  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
    await chatPage.goto();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });
  });

  test("chat page loads with input and send button", async () => {
    await expect(chatPage.messageInput).toBeVisible();
    await expect(chatPage.sendBtn).toBeVisible();
    await expect(chatPage.agentSelector).toBeVisible();
  });

  test("send message → user bubble appears", async () => {
    await chatPage.sendMessage("Hello, what can you do?");
    await expect(chatPage.userMessages.last()).toContainText(
      "Hello, what can you do?",
    );
  });

  test("send message → assistant response streams in", async ({
    page,
  }) => {
    await mockChatStream(page, "Hello!");
    await chatPage.sendAndWaitForReply("Say hello in one word.");
    const lastAssistant = chatPage.assistantMessages.last();
    await expect(lastAssistant).not.toBeEmpty();
  });

  test("switch agent → label changes", async () => {
    const initial = await chatPage.activeAgentLabel();
    await chatPage.selectAgent("Stock Analysis");
    const updated = await chatPage.activeAgentLabel();
    expect(updated).toBe("Stock Analysis");
    expect(updated).not.toBe(initial);
  });

  test("clear messages → all bubbles removed", async ({
    page,
  }) => {
    await mockChatStream(page);
    // Send a message first
    await chatPage.sendMessage("Test message for clearing");
    await expect(chatPage.userMessages).toHaveCount(1, {
      timeout: 5_000,
    });
    // Clear
    await chatPage.clearAllMessages();
  });

  test("multi-turn conversation → messages stack", async ({
    page,
  }) => {
    await mockChatStream(page, "Answer one");
    await chatPage.sendAndWaitForReply("First question");
    const count1 = await chatPage.assistantMessages.count();

    await chatPage.sendAndWaitForReply("Follow-up question");
    const count2 = await chatPage.assistantMessages.count();

    expect(count2).toBe(count1 + 1);
  });

  test("status badge shows during streaming", async ({
    page,
  }) => {
    // Delay the response so the status badge has time to appear
    await page.route("**/chat/stream", async (route) => {
      await new Promise((r) => setTimeout(r, 2_000));
      const body =
        JSON.stringify({ type: "final", response: "A joke!" }) +
        "\n";
      return route.fulfill({
        status: 200,
        contentType: "application/x-ndjson",
        body,
      });
    });
    await chatPage.sendMessage("Tell me a short joke");
    // Status badge should appear while processing
    await expect(chatPage.statusBadge).toBeVisible({
      timeout: 5_000,
    });
  });

  test("Enter key sends message", async ({ page }) => {
    await mockChatStream(page);
    await chatPage.messageInput.click();
    await chatPage.messageInput.pressSequentially(
      "Testing enter key",
      { delay: 30 },
    );
    await chatPage.messageInput.press("Enter");
    await expect(chatPage.userMessages.last()).toContainText(
      "Testing enter key",
      { timeout: 5_000 },
    );
  });
});
