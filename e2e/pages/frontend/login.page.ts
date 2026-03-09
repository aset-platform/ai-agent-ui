/**
 * Page object for the Next.js login page (``/login``).
 */

import { expect } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class LoginPage extends BasePage {
  get emailInput() {
    return this.tid(FE.loginEmail);
  }
  get passwordInput() {
    return this.tid(FE.loginPassword);
  }
  get submitBtn() {
    return this.tid(FE.loginSubmit);
  }
  get errorMessage() {
    return this.tid(FE.loginError);
  }
  get googleOAuthBtn() {
    return this.tid(FE.oauthGoogle);
  }

  async goto(): Promise<void> {
    await super.goto("/login");
  }

  /** Fill credentials and submit the login form. */
  async login(email: string, password: string): Promise<void> {
    await this.emailInput.waitFor({ state: "visible" });
    await this.emailInput.click();
    await this.emailInput.pressSequentially(email, {
      delay: 20,
    });
    await this.passwordInput.click();
    await this.passwordInput.pressSequentially(password, {
      delay: 20,
    });
    await expect(this.submitBtn).toBeEnabled({
      timeout: 5_000,
    });
    await this.submitBtn.click();
  }

  /** Log in and wait for redirect to the chat page. */
  async loginAndWaitForChat(
    email: string,
    password: string,
  ): Promise<void> {
    await this.login(email, password);
    await this.page.waitForURL("/", { timeout: 30_000 });
  }

  /** Assert that an error message is visible. */
  async expectError(text?: string): Promise<void> {
    await expect(this.errorMessage).toBeVisible();
    if (text) {
      await expect(this.errorMessage).toContainText(text);
    }
  }
}
