/**
 * E2E tests for chat tool invocations (Gap 4).
 *
 * Verifies that agent tool calls (stock analysis, forecast,
 * portfolio) produce meaningful responses. These tests hit
 * the live backend and are slow (LLM + tool execution).
 */

import { test, expect } from "@playwright/test";

import { ChatPage } from "../../pages/frontend/chat.page";

test.describe("Chat tool invocations", () => {
  test.describe.configure({ mode: "serial" });
  test.slow(); // LLM calls take 30-60s

  let chatPage: ChatPage;

  test.beforeEach(async ({ page }) => {
    chatPage = new ChatPage(page);
    await chatPage.goto();
    await expect(chatPage.messageInput).toBeVisible({
      timeout: 15_000,
    });
  });

  test("stock analysis query returns data", async () => {
    await chatPage.sendAndWaitForReply(
      "Analyze RELIANCE.NS stock price",
      60_000,
    );
    const last = chatPage.assistantMessages.last();
    await expect(last).toBeVisible({ timeout: 10_000 });
    const text = await last.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("forecast query returns prediction", async () => {
    await chatPage.sendAndWaitForReply(
      "Forecast TCS.NS for 30 days",
      60_000,
    );
    const last = chatPage.assistantMessages.last();
    await expect(last).toBeVisible({ timeout: 10_000 });
    const text = await last.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("portfolio query returns holdings", async () => {
    await chatPage.sendAndWaitForReply(
      "Show my portfolio summary",
      60_000,
    );
    const last = chatPage.assistantMessages.last();
    await expect(last).toBeVisible({ timeout: 10_000 });
    const text = await last.textContent();
    expect(text?.length).toBeGreaterThan(20);
  });

  test("unknown ticker handles gracefully", async () => {
    await chatPage.sendAndWaitForReply(
      "Analyze ZZZZZ stock",
      60_000,
    );
    const last = chatPage.assistantMessages.last();
    await expect(last).toBeVisible({ timeout: 10_000 });
    // Should get a response (not crash)
    const text = await last.textContent();
    expect(text?.length).toBeGreaterThan(0);
  });
});
