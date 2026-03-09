/**
 * Page object for the navigation grid menu.
 */

import { expect } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class NavigationPage extends BasePage {
  get menuToggle() {
    return this.tid(FE.navMenuToggle);
  }

  navItem(name: string) {
    return this.tid(FE.navItem(name));
  }

  /** Open the grid navigation menu. */
  async openMenu(): Promise<void> {
    await this.menuToggle.click();
  }

  /** Navigate to a named view (chat, docs, dashboard, etc). */
  async navigateTo(
    name: string,
  ): Promise<void> {
    await this.openMenu();
    const item = this.navItem(name);
    await expect(item).toBeVisible();
    await item.click();
  }
}
