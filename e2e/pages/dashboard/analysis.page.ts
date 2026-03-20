/**
 * Page object for the Dash analysis page (``/analysis``).
 */

import { type Locator, expect } from "@playwright/test";

import { DASH } from "../../utils/selectors";
import {
  gotoDashPage,
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
    await gotoDashPage(
      this.page,
      `/analysis?token=${token}`,
    );
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
