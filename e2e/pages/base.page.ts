/**
 * Abstract base page object for all POM classes.
 *
 * Provides shared navigation, locator helpers, and wait utilities
 * that every page object inherits.
 */

import { type Locator, type Page, expect } from "@playwright/test";

export abstract class BasePage {
  protected readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  /** Navigate to a path relative to the project's baseURL. */
  async goto(path = "/"): Promise<void> {
    await this.page.goto(path, { waitUntil: "commit" });
  }

  /** Shorthand for ``this.page.locator(selector)``. */
  protected loc(selector: string): Locator {
    return this.page.locator(selector);
  }

  /** Shorthand for ``this.page.getByTestId(id)``. */
  protected tid(testId: string): Locator {
    return this.page.getByTestId(testId);
  }

  /** Wait for a loader/spinner to disappear. */
  async waitForLoaderGone(
    locator: Locator,
    timeout = 10_000,
  ): Promise<void> {
    await expect(locator).toBeHidden({ timeout });
  }

  /** Get the current page URL path. */
  get currentPath(): string {
    return new URL(this.page.url()).pathname;
  }
}
