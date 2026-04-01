/**
 * Page object for the analytics/analysis page
 * (``/analytics/analysis``) with 5 tabs.
 */

import { type Locator } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class AnalyticsPage extends BasePage {
  // ── Navigation ─────────────────────────────────────

  /** Navigate to the analytics analysis page. */
  async gotoAnalysis(): Promise<void> {
    await this.page.goto("/analytics/analysis", {
      waitUntil: "domcontentloaded",
    });
    // Wait for at least one tab to render
    await this.page
      .locator("[data-testid^='analytics-tab-']")
      .first()
      .waitFor({ state: "visible", timeout: 15_000 });
  }

  // ── Tab navigation ─────────────────────────────────

  /** Click a tab by its identifier string. */
  async clickTab(tabId: string): Promise<void> {
    await this.tid(FE.analyticsTab(tabId)).click();
  }

  /** Return the inner text of the currently active tab. */
  async activeTabText(): Promise<string> {
    const tabs = this.page.locator(
      "[data-testid^='analytics-tab-']",
    );
    const count = await tabs.count();
    for (let i = 0; i < count; i++) {
      const tab = tabs.nth(i);
      const cls = await tab.getAttribute("class") || "";
      if (cls.includes("text-indigo-600")) {
        return tab.innerText();
      }
    }
    return "";
  }

  // ── Portfolio Analysis tab ─────────────────────────

  /** Select a time period for portfolio analysis. */
  async selectPeriod(period: string): Promise<void> {
    await this.tid(
      FE.portfolioAnalysisPeriod(period),
    ).click();
  }

  /** Click the portfolio analysis refresh button. */
  async clickPortfolioRefresh(): Promise<void> {
    await this.tid(FE.portfolioAnalysisRefreshBtn).click();
  }

  /** Locator for the portfolio analysis chart container. */
  portfolioChartContainer(): Locator {
    return this.tid(FE.portfolioAnalysisChart);
  }

  /** Locator for a named portfolio metric value. */
  portfolioMetricValue(name: string): Locator {
    return this.tid(FE.portfolioAnalysisMetricValue(name));
  }

  /** Locator for the portfolio currency badge. */
  portfolioCurrencyBadge(): Locator {
    return this.tid(FE.portfolioAnalysisCurrencyBadge);
  }

  /** Locator for the portfolio empty state. */
  portfolioEmptyState(): Locator {
    return this.tid(FE.portfolioAnalysisEmpty);
  }

  /** Locator for the portfolio error state. */
  portfolioErrorState(): Locator {
    return this.tid(FE.portfolioAnalysisError);
  }

  // ── Portfolio Forecast tab ─────────────────────────

  /** Select a forecast horizon (months). */
  async selectHorizon(months: 3 | 6 | 9): Promise<void> {
    await this.tid(
      FE.portfolioForecastHorizon(months),
    ).click();
  }

  /** Click the portfolio forecast refresh button. */
  async clickForecastRefresh(): Promise<void> {
    await this.tid(FE.portfolioForecastRefreshBtn).click();
  }

  /** Locator for the portfolio forecast chart container. */
  forecastChartContainer(): Locator {
    return this.tid(FE.portfolioForecastChart);
  }

  /** Locator for a named forecast card value. */
  forecastCardValue(name: string): Locator {
    return this.tid(FE.portfolioForecastCardValue(name));
  }

  /** Locator for the forecast P&L text. */
  forecastPnlText(): Locator {
    return this.tid(FE.portfolioForecastCardPnl);
  }

  /** Locator for the "Predicted" label on forecast. */
  forecastPredictedLabel(): Locator {
    return this.tid(FE.portfolioForecastChart).locator(
      "[data-label='predicted']",
    );
  }

  /** Locator for the forecast empty state. */
  forecastEmptyState(): Locator {
    return this.tid(FE.portfolioForecastEmpty);
  }

  /** Locator for the forecast error state. */
  forecastErrorState(): Locator {
    return this.tid(FE.portfolioForecastError);
  }

  // ── Stock Analysis tab ─────────────────────────────

  /** Select a date range for stock analysis. */
  async selectDateRange(period: string): Promise<void> {
    await this.tid(FE.stockAnalysisRange(period)).click();
  }

  /** Select a candle interval for stock analysis. */
  async selectInterval(interval: string): Promise<void> {
    await this.tid(
      FE.stockAnalysisInterval(interval),
    ).click();
  }

  /** Toggle a chart indicator on or off. */
  async toggleIndicator(name: string): Promise<void> {
    await this.tid(FE.stockAnalysisIndicator(name)).click();
  }

  /** Locator for the stock analysis chart container. */
  stockChartContainer(): Locator {
    return this.tid(FE.stockAnalysisChart);
  }

  /** Locator for the stock analysis error state. */
  stockErrorState(): Locator {
    return this.tid(FE.stockAnalysisError);
  }

  // ── Stock Forecast tab ─────────────────────────────

  /** Select a forecast horizon for stock forecast. */
  async selectForecastHorizon(
    months: 3 | 6 | 9,
  ): Promise<void> {
    await this.tid(
      FE.stockForecastHorizon(months),
    ).click();
  }

  /** Locator for the stock forecast chart container. */
  forecastStockChartContainer(): Locator {
    return this.tid(FE.stockForecastChart);
  }

  /** Locator for a forecast target card by index. */
  forecastTargetCard(index: number): Locator {
    return this.tid(FE.stockForecastTargetCard(index));
  }

  /** Locator for a named forecast accuracy metric. */
  forecastAccuracyMetric(name: string): Locator {
    return this.tid(FE.stockForecastAccuracy(name));
  }

  /** Locator for the stock forecast error state. */
  forecastStockErrorState(): Locator {
    return this.tid(FE.stockForecastError);
  }

  // ── Compare tab ────────────────────────────────────

  /** Locator for the compare chart container. */
  compareChartContainer(): Locator {
    return this.tid(FE.compareChart);
  }

  /** Locator for the compare ticker select control. */
  compareTickerSelect(): Locator {
    return this.tid(FE.compareTickerSelect);
  }

  /** Locator for the compare empty state. */
  compareEmptyState(): Locator {
    return this.tid(FE.compareEmpty);
  }
}
