/**
 * Page object for the Dash analysis page (``/analysis``).
 */

import { type Locator, expect } from "@playwright/test";

import { DASH } from "../../utils/selectors";
import {
  waitForDashLoading,
  waitForPlotlyChart,
} from "../../utils/wait.helper";
import { BasePage } from "../base.page";

export class DashAnalysisPage extends BasePage {
  get tickerDropdown() {
    return this.loc("#analysis-ticker-dropdown");
  }
  get tabs() {
    return this.tid(DASH.analysisTabs);
  }
  get refreshBtn() {
    return this.loc(`#${DASH.analysisRefreshBtn}`);
  }
  get refreshStatus() {
    return this.loc(`#${DASH.analysisRefreshStatus}`);
  }

  /** Navigate to analysis page with JWT. */
  async gotoWithToken(token: string): Promise<void> {
    await this.page.goto(`/analysis?token=${token}`);
    await waitForDashLoading(this.page);
    // Retry once if Dash restarted mid-load
    const err = this.page.locator("text=Callback error");
    if ((await err.count()) > 0) {
      await this.page.waitForTimeout(3_000);
      await this.page.reload();
      await waitForDashLoading(this.page);
    }
  }

  /** Select a ticker from the dropdown. */
  async selectTicker(ticker: string): Promise<void> {
    await this.tickerDropdown.click();
    await this.page.locator(`[role="option"]`).filter({
      hasText: ticker,
    }).click();
    await waitForDashLoading(this.page);
  }

  /** Click a tab by its visible label text. */
  async clickTab(label: string): Promise<void> {
    await this.page
      .getByRole("tab", { name: label })
      .click();
    await waitForDashLoading(this.page);
  }

  /** Click the refresh button and wait for result. */
  async refreshAndWait(timeout = 60_000): Promise<void> {
    await this.refreshBtn.click();
    await expect(this.refreshStatus).not.toBeEmpty({
      timeout,
    });
  }
}
