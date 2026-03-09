/**
 * Page object for the Dash marketplace page (``/marketplace``).
 */

import { type Locator, expect } from "@playwright/test";

import { DASH } from "../../utils/selectors";
import { waitForDashLoading } from "../../utils/wait.helper";
import { BasePage } from "../base.page";

export class DashMarketplacePage extends BasePage {
  get grid() {
    return this.tid(DASH.marketplaceGrid);
  }
  get searchInput() {
    return this.loc(`#${DASH.marketplaceSearch}`);
  }

  /** Navigate to marketplace page with JWT. */
  async gotoWithToken(token: string): Promise<void> {
    await this.page.goto(`/marketplace?token=${token}`);
    await waitForDashLoading(this.page);
    // Retry once if Dash restarted mid-load
    const err = this.page.locator("text=Callback error");
    if ((await err.count()) > 0) {
      await this.page.waitForTimeout(3_000);
      await this.page.reload();
      await waitForDashLoading(this.page);
    }
  }

  /** Get all "Add" buttons on the current page. */
  get addButtons(): Locator {
    return this.page
      .getByRole("button", { name: /add/i });
  }

  /** Get all "Remove" buttons on the current page. */
  get removeButtons(): Locator {
    return this.page
      .getByRole("button", { name: /remove/i });
  }

  /** Click the Add button for a specific ticker. */
  async addTicker(ticker: string): Promise<void> {
    const row = this.page.locator(`text=${ticker}`).first();
    const addBtn = row
      .locator("..")
      .getByRole("button", { name: /add/i });
    await addBtn.click();
    await waitForDashLoading(this.page);
  }
}
