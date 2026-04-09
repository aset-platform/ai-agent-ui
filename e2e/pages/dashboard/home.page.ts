/**
 * Page object for the Dash home page (stock cards overview).
 */

import { type Locator, expect } from "@playwright/test";

import { DASH } from "../../utils/selectors";
import {
  gotoDashPage,
  waitForDashLoading,
  waitForPlotlyChart,
} from "../../utils/wait.helper";
import { BasePage } from "../base.page";

export class DashHomePage extends BasePage {
  get tickerSearch() {
    return this.loc(`#${DASH.tickerSearch}`);
  }
  get analyseBtn() {
    return this.loc(`#${DASH.analyseBtn}`);
  }
  get registryDropdown() {
    return this.loc(`#${DASH.registryDropdown}`);
  }
  get stockCardsGrid() {
    return this.tid(DASH.stockCardsGrid);
  }
  get filterIndia() {
    return this.loc(`#${DASH.filterIndia}`);
  }
  get filterUS() {
    return this.loc(`#${DASH.filterUS}`);
  }
  get pagination() {
    return this.loc(`#${DASH.pagination}`);
  }

  /** Navigate to home with JWT token as URL param. */
  async gotoWithToken(token: string): Promise<void> {
    await gotoDashPage(this.page, `/?token=${token}`);
  }

  /** All stock card elements on the current page. */
  get stockCards(): Locator {
    return this.page.locator(".stock-card");
  }

  /** Get the count of visible stock cards. */
  async getCardCount(): Promise<number> {
    await waitForDashLoading(this.page);
    return this.stockCards.count();
  }

  /** Type a ticker and click Analyse to navigate. */
  async searchTicker(ticker: string): Promise<void> {
    await this.tickerSearch.fill(ticker);
    await this.analyseBtn.click();
    await this.page.waitForURL(/\/analysis/, {
      timeout: 10_000,
    });
  }

  /** Click a specific card's refresh button. */
  cardRefreshBtn(ticker: string): Locator {
    return this.page.locator(
      `[id*="card-refresh-btn"][id*='"${ticker}"']`,
    );
  }

  /** Click a stock card to navigate to analysis. */
  async clickCard(ticker: string): Promise<void> {
    const card = this.tid(DASH.stockCard(ticker));
    await card.click();
    await this.page.waitForURL(/\/analysis/, {
      timeout: 10_000,
    });
  }
}
