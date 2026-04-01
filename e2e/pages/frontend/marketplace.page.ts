/**
 * Page object for the Marketplace page
 * (``/analytics/marketplace``).
 */

import { type Locator } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class MarketplacePage extends BasePage {
  /** Navigate to the marketplace page. */
  async gotoMarketplace(): Promise<void> {
    await super.goto("/analytics/marketplace");
  }

  /** Type a query into the ticker search box. */
  async searchTicker(query: string): Promise<void> {
    const input = this.tid(FE.marketplaceSearch);
    await input.clear();
    await input.fill(query);
  }

  /** Click a market filter button. */
  async selectMarket(
    filter: string,
  ): Promise<void> {
    await this.tid(
      FE.marketplaceMarket(filter),
    ).click();
  }

  /** Click the "Link" button for a ticker. */
  async linkTicker(ticker: string): Promise<void> {
    await this.tid(
      FE.marketplaceLink(ticker),
    ).click();
  }

  /** Click the "Unlink" button for a ticker. */
  async unlinkTicker(
    ticker: string,
  ): Promise<void> {
    await this.tid(
      FE.marketplaceUnlink(ticker),
    ).click();
  }

  /** The marketplace data table. */
  marketplaceTable(): Locator {
    return this.tid(FE.marketplaceTable);
  }

  /** A single row by ticker symbol. */
  marketplaceRow(ticker: string): Locator {
    return this.tid(FE.marketplaceRow(ticker));
  }

  /** Stats bar showing linked/total counts. */
  statsBar(): Locator {
    return this.tid(FE.marketplaceStats);
  }

  /** Pagination info text (e.g. "Page 1 of 3"). */
  pageInfo(): Locator {
    return this.tid(FE.marketplacePageInfo);
  }

  /** Previous-page pagination button. */
  prevButton(): Locator {
    return this.tid(FE.marketplacePaginationPrev);
  }

  /** Next-page pagination button. */
  nextButton(): Locator {
    return this.tid(FE.marketplacePaginationNext);
  }
}
