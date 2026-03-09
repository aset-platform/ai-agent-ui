/**
 * Page object for the Next.js chat interface (``/``).
 */

import { type Locator, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class ChatPage extends BasePage {
  get messageInput() {
    return this.tid(FE.chatInput);
  }
  get sendBtn() {
    return this.tid(FE.chatSend);
  }
  get agentSelector() {
    return this.tid(FE.agentSelector);
  }
  get clearBtn() {
    return this.tid(FE.clearMessages);
  }
  get profileAvatar() {
    return this.tid(FE.profileAvatar);
  }
  get statusBadge() {
    return this.tid(FE.statusBadge);
  }

  /** All assistant message bubbles. */
  get assistantMessages(): Locator {
    return this.tid(FE.assistantMessage);
  }

  /** All user message bubbles. */
  get userMessages(): Locator {
    return this.tid(FE.userMessage);
  }

  async goto(): Promise<void> {
    await super.goto("/");
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

  /** Switch to a different agent by visible label. */
  async selectAgent(agentName: string): Promise<void> {
    const btn = this.agentSelector.getByRole("button", {
      name: agentName,
    });
    await btn.click();
    // Wait for the active class to settle on the clicked button
    await expect(btn).toHaveClass(/bg-white/, {
      timeout: 3_000,
    });
  }

  /** Get the currently active agent button label. */
  async activeAgentLabel(): Promise<string> {
    const active = this.agentSelector.locator(
      "button.bg-white",
    );
    return active.innerText();
  }

  /** Clear all messages and assert the list is empty. */
  async clearAllMessages(): Promise<void> {
    await this.clearBtn.click();
    await expect(this.assistantMessages).toHaveCount(0);
    await expect(this.userMessages).toHaveCount(0);
  }
}
