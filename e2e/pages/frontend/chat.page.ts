/**
 * Page object for the chat side-panel on ``/dashboard``.
 *
 * Chat is a collapsible panel (not a full page). All chat
 * locators are scoped to ``data-testid="chat-panel"``.
 */

import { type Locator, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class ChatPage extends BasePage {
  /** Desktop chat panel — scopes all chat locators. */
  get panel(): Locator {
    return this.page.getByTestId("chat-panel");
  }

  get messageInput() {
    return this.panel.getByTestId(FE.chatInput);
  }
  get sendBtn() {
    return this.panel.getByTestId(FE.chatSend);
  }
  get clearBtn() {
    return this.panel.getByTestId(FE.clearMessages);
  }
  /** Profile avatar lives in AppHeader, not chat panel. */
  get profileAvatar() {
    return this.page.getByTestId(FE.profileAvatar);
  }
  get statusBadge() {
    return this.panel.getByTestId(FE.statusBadge);
  }

  /** All assistant message bubbles. */
  get assistantMessages(): Locator {
    return this.panel.getByTestId(FE.assistantMessage);
  }

  /** All user message bubbles. */
  get userMessages(): Locator {
    return this.panel.getByTestId(FE.userMessage);
  }

  async goto(): Promise<void> {
    await super.goto("/dashboard");
    // Chat panel is collapsed by default — open it.
    const toggle = this.page.getByRole("button", {
      name: "Toggle chat panel",
    });
    await expect(toggle).toBeVisible({ timeout: 10_000 });
    await toggle.click();
    await this.messageInput.waitFor({
      state: "visible",
      timeout: 10_000,
    });
  }

  /** Type a message and send it. */
  async sendMessage(text: string): Promise<void> {
    await this.messageInput.click();
    await this.messageInput.pressSequentially(text, {
      delay: 30,
    });
    // Wait for React state to enable the send button
    await expect(this.sendBtn).toBeEnabled({ timeout: 5_000 });
    await this.sendBtn.click();
  }

  /** Send a message and wait for an assistant response. */
  async sendAndWaitForReply(
    text: string,
    timeout = 30_000,
  ): Promise<void> {
    const countBefore = await this.assistantMessages.count();
    await this.sendMessage(text);
    await expect(this.assistantMessages).toHaveCount(
      countBefore + 1,
      { timeout },
    );
  }

  /** Clear all messages and assert the list is empty. */
  async clearAllMessages(): Promise<void> {
    await this.clearBtn.click();
    await expect(this.assistantMessages).toHaveCount(0);
    await expect(this.userMessages).toHaveCount(0);
  }
}
