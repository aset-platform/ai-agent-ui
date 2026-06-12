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

  /** The watchlist ⋮ overflow trigger button. */
  watchlistOverflowBtn(): Locator {
    return this.tid(FE.dashboardWatchlistOverflowButton);
  }

  /** The overflow dropdown menu. */
  watchlistOverflowMenu(): Locator {
    return this.tid(FE.dashboardWatchlistOverflowMenu);
  }

  /** Open the watchlist ⋮ overflow menu. */
  async openWatchlistOverflow(): Promise<void> {
    await this.watchlistOverflowBtn().click();
    await this.watchlistOverflowMenu().waitFor({
      state: "visible",
      timeout: 5_000,
    });
  }

  /** "Build RSI(2) replay fixture" menu item. */
  buildFixtureItem(): Locator {
    return this.tid(FE.watchlistBuildFixture);
  }

  /** Inline result message shown after the fixture build. */
  buildFixtureResult(): Locator {
    return this.tid(FE.watchlistBuildFixtureResult);
  }
}
