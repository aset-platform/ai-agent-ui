/**
 * Page object for the Advanced Analytics page
 * (``/advanced-analytics``).  Sprint 9 AA-13.
 */

import { type Locator, expect } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export type AdvancedReport =
  | "current-day-upmove"
  | "previous-day-breakout"
  | "mom-volume-delivery"
  | "wow-volume-delivery"
  | "two-day-scan"
  | "three-day-scan"
  | "top-50-delivery-by-qty";

export class AdvancedAnalyticsPage extends BasePage {
  /** Navigate to the page (default tab). */
  async gotoAdvancedAnalytics(): Promise<void> {
    await super.goto("/advanced-analytics");
    await expect(this.heading()).toBeVisible({ timeout: 10_000 });
  }

  /** Navigate directly to a tab via URL ``?tab=`` sync. */
  async gotoTab(report: AdvancedReport): Promise<void> {
    await super.goto(`/advanced-analytics?tab=${report}`);
    await expect(this.heading()).toBeVisible({ timeout: 10_000 });
  }

  /** Click the tab strip button for *report*. */
  async switchTab(report: AdvancedReport): Promise<void> {
    await this.tid(FE.advancedAnalyticsTab(report)).click();
  }

  /** Wait for the table for *report* to be attached. */
  async waitForTable(report: AdvancedReport): Promise<void> {
    await this.tid(FE.advancedAnalyticsTable(report)).waitFor({
      state: "attached",
      timeout: 10_000,
    });
  }

  /** Heading locator (LCP candidate per AA-10). */
  heading(): Locator {
    return this.tid(FE.advancedAnalyticsHeading);
  }

  /** Tab strip container. */
  tabs(): Locator {
    return this.tid(FE.advancedAnalyticsTabs);
  }

  /** Active panel locator for *report*. */
  panel(report: AdvancedReport): Locator {
    return this.tid(FE.advancedAnalyticsPanel(report));
  }

  /** The shared table for *report*. */
  table(report: AdvancedReport): Locator {
    return this.tid(FE.advancedAnalyticsTable(report));
  }

  /** Stale-ticker amber chip for *report*. */
  staleChip(report: AdvancedReport): Locator {
    return this.tid(FE.advancedAnalyticsStaleChip(report));
  }

  /** Click the column-selector trigger button. */
  async openColumnSelector(): Promise<void> {
    // The ColumnSelector trigger is a button with text
    // ``Columns (n/56)`` — scoped to the page main area
    // to avoid matching the insights selector elsewhere.
    await this.page
      .getByRole("button", { name: /^Columns \(/ })
      .first()
      .click();
  }

  /** CSV download button. */
  csvButton(): Locator {
    return this.page.getByTestId("download-csv");
  }

  /** Get the count of body rows in the active table. */
  async getRowCount(report: AdvancedReport): Promise<number> {
    return this.table(report).locator("tbody tr").count();
  }

  /** Click a column header to sort. */
  async sortBy(key: string): Promise<void> {
    await this.tid(FE.advancedAnalyticsSort(key)).click();
  }

  /** Pagination — next / prev. */
  async clickNext(report: AdvancedReport): Promise<void> {
    await this.tid(FE.advancedAnalyticsNext(report)).click();
  }
  async clickPrev(report: AdvancedReport): Promise<void> {
    await this.tid(FE.advancedAnalyticsPrev(report)).click();
  }

  /** Navigate directly to the Swing Setups tab. */
  async gotoSwingSetups(): Promise<void> {
    await super.goto("/advanced-analytics?tab=swing-setups");
    await expect(this.heading()).toBeVisible({ timeout: 10_000 });
  }

  /** Regime-pill locator. */
  swingPill(
    regime: "bull" | "sideways" | "bearish",
  ): Locator {
    return this.tid(FE.swingRegimePill(regime));
  }

  /** Methodology panel locator. */
  swingMethodologyPanel(): Locator {
    return this.tid(FE.swingMethodologyPanel);
  }

  /** Collapse/expand toggle locator. */
  swingMethodologyToggle(): Locator {
    return this.tid(FE.swingMethodologyToggle);
  }
}
