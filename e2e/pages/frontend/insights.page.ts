/**
 * Page object for the Insights page
 * (``/analytics/insights``).
 */

import { type Locator } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class InsightsPage extends BasePage {
  /** Navigate to the insights page. */
  async gotoInsights(): Promise<void> {
    await super.goto("/analytics/insights");
  }

  /** Click a tab by its identifier. */
  async clickTab(tabId: string): Promise<void> {
    await this.tid(FE.insightsTab(tabId)).click();
  }

  /** Select a value in the market filter dropdown. */
  async selectMarketFilter(
    value: string,
  ): Promise<void> {
    await this.tid(FE.insightsMarketFilter)
      .selectOption(value);
  }

  /** Select a value in the sector filter dropdown. */
  async selectSectorFilter(
    value: string,
  ): Promise<void> {
    await this.tid(FE.insightsSectorFilter)
      .selectOption(value);
  }

  /** Select a value in the RSI filter dropdown. */
  async selectRsiFilter(
    value: string,
  ): Promise<void> {
    await this.tid(FE.insightsRsiFilter)
      .selectOption(value);
  }

  /** Select a value in the period filter dropdown. */
  async selectPeriodFilter(
    value: string,
  ): Promise<void> {
    await this.tid(FE.insightsPeriodFilter)
      .selectOption(value);
  }

  /** Select the financial statement type. */
  async selectStatementType(
    value: string,
  ): Promise<void> {
    await this.tid(FE.insightsStatementType)
      .selectOption(value);
  }

  /** The insights data table. */
  insightsTable(): Locator {
    return this.tid(FE.insightsTable);
  }

  /** The insights chart container. */
  insightsChart(): Locator {
    return this.tid(FE.insightsChart);
  }

  /** Empty-state placeholder. */
  insightsEmpty(): Locator {
    return this.tid(FE.insightsEmpty);
  }
}
