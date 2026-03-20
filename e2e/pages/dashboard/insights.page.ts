/**
 * Page object for the Dash insights page (``/insights``).
 */

import { type Locator, expect } from "@playwright/test";

import { DASH } from "../../utils/selectors";
import {
  gotoDashPage,
  waitForDashLoading,
  waitForPlotlyChart,
} from "../../utils/wait.helper";
import { BasePage } from "../base.page";

/** The seven insight tab IDs used by ``dbc.Tabs``. */
const TAB_IDS = [
  "screener-tab",
  "targets-tab",
  "dividends-tab",
  "risk-tab",
  "sectors-tab",
  "correlation-tab",
  "quarterly-tab",
] as const;

/** Friendly labels that match the ``dbc.Tab(label=...)`` values. */
const TAB_LABELS = [
  "Screener",
  "Price Targets",
  "Dividends",
  "Risk Metrics",
  "Sectors",
  "Correlation",
  "Quarterly",
] as const;

export type InsightsTabLabel = (typeof TAB_LABELS)[number];

export class DashInsightsPage extends BasePage {
  get tabs() {
    return this.loc("#insights-tabs");
  }

  /** Navigate to insights page with JWT. */
  async gotoWithToken(token: string): Promise<void> {
    await gotoDashPage(
      this.page,
      `/insights?token=${token}`,
    );
  }

  /** Click a tab by its visible label text. */
  async clickTab(label: InsightsTabLabel): Promise<void> {
    await this.page
      .getByRole("tab", { name: label })
      .click();
    await waitForDashLoading(this.page);
  }

  /** Get all visible tab elements. */
  get tabButtons(): Locator {
    return this.tabs.getByRole("tab");
  }

  /** Wait for a Plotly chart to render within the page. */
  async waitForChart(timeout = 30_000): Promise<void> {
    await this.page
      .locator(".js-plotly-plot")
      .first()
      .waitFor({ state: "visible", timeout });
  }

  /** Get the active tab label. */
  async activeTabLabel(): Promise<string> {
    const active = this.tabs.locator(
      ".nav-link.active",
    );
    return active.innerText();
  }

  /** All tab labels as defined in the layout. */
  static readonly TAB_LABELS = TAB_LABELS;
  static readonly TAB_IDS = TAB_IDS;
}
