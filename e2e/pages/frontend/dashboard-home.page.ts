/**
 * Page object for the Dashboard Home page (``/dashboard``).
 */

import { type Locator } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class DashboardHomePage extends BasePage {
  /** Navigate to the dashboard home page. */
  async gotoDashboard(): Promise<void> {
    await super.goto("/dashboard");
  }

  /** Click a market-filter button (india / us). */
  async switchMarketFilter(
    market: "india" | "us",
  ): Promise<void> {
    await this.tid(
      FE.dashboardMarketFilter(market),
    ).click();
  }

  /** Hero section: total portfolio value. */
  portfolioValue(): Locator {
    return this.tid(FE.dashboardHeroPortfolioValue);
  }

  /** Hero section: daily P&L change. */
  dailyChange(): Locator {
    return this.tid(FE.dashboardHeroDailyChange);
  }

  /** The watchlist table container. */
  watchlistTable(): Locator {
    return this.tid(FE.dashboardWatchlistTable);
  }

  /** A single row in the watchlist by ticker symbol. */
  watchlistRow(ticker: string): Locator {
    return this.tid(FE.dashboardWatchlistRow(ticker));
  }

  /** Per-ticker refresh button in the watchlist. */
  watchlistRefreshBtn(ticker: string): Locator {
    return this.tid(
      FE.dashboardWatchlistRefresh(ticker),
    );
  }

  /** The "Add Stock" button. */
  addStockBtn(): Locator {
    return this.tid(FE.dashboardAddStockBtn);
  }

  /** The forecast summary widget. */
  forecastWidget(): Locator {
    return this.tid(FE.dashboardForecastWidget);
  }
}
