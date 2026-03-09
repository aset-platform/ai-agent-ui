/**
 * Page object for the Dash forecast page (``/forecast``).
 */

import { type Locator, expect } from "@playwright/test";

import { DASH } from "../../utils/selectors";
import {
  waitForDashLoading,
  waitForPlotlyChart,
} from "../../utils/wait.helper";
import { BasePage } from "../base.page";

export class DashForecastPage extends BasePage {
  get tickerDropdown() {
    return this.loc(`#${DASH.forecastTickerDropdown}`);
  }
  get horizonRadio() {
    return this.loc(`#${DASH.forecastHorizonRadio}`);
  }
  get refreshBtn() {
    return this.loc(`#${DASH.forecastRefreshBtn}`);
  }
  get chartContainer() {
    return this.tid(DASH.forecastChart);
  }
  get accuracyRow() {
    return this.tid(DASH.forecastAccuracy);
  }

  /** Navigate to forecast page with JWT. */
  async gotoWithToken(token: string): Promise<void> {
    await this.page.goto(`/forecast?token=${token}`);
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
    await this.page
      .locator(`[role="option"]`)
      .filter({ hasText: ticker })
      .click();
    await waitForDashLoading(this.page);
  }

  /** Click the refresh button and wait for chart. */
  async refreshAndWaitForChart(
    timeout = 90_000,
  ): Promise<void> {
    await this.refreshBtn.click();
    await waitForPlotlyChart(
      this.page,
      `[data-testid="${DASH.forecastChart}"]`,
      timeout,
    );
  }
}
