/**
 * Portfolio fixture for analytics E2E tests.
 *
 * Seeds the general test user (test@demo.com) with portfolio
 * holdings for tickers that already have OHLCV data from
 * prior analysis runs.  This ensures portfolio performance
 * and forecast endpoints return real data.
 */

import { request as pwRequest } from "@playwright/test";
import { test as authTest } from "./auth.fixture";
import {
  apiAddPortfolioHolding,
  apiGetPortfolio,
} from "../utils/api.helper";

/**
 * Stocks to seed — must be tickers the test user already
 * has linked with OHLCV data.  Using a subset of the 14
 * tickers linked to test@demo.com.
 */
const INDIA_STOCKS = [
  { ticker: "RELIANCE.NS", quantity: 10, price: 2450 },
  { ticker: "TCS.NS", quantity: 5, price: 3800 },
  { ticker: "AXISBANK.NS", quantity: 15, price: 1100 },
  { ticker: "BEL.NS", quantity: 30, price: 280 },
  { ticker: "BPCL.NS", quantity: 20, price: 350 },
] as const;

const US_STOCKS = [
  { ticker: "AAPL", quantity: 10, price: 178 },
  { ticker: "MSFT", quantity: 8, price: 380 },
  { ticker: "TSLA", quantity: 4, price: 245 },
] as const;

export const ALL_STOCKS = [...INDIA_STOCKS, ...US_STOCKS];

type PortfolioFixtures = {
  seededPortfolio: boolean;
};

/**
 * Extended test with pre-seeded portfolio.
 *
 * Uses the general user (test@demo.com) which has existing
 * OHLCV data — NOT the admin user.  The analytics-chromium
 * project uses superuser.json storageState, so we override
 * the token used for seeding to use userToken.
 */
export const test = authTest.extend<PortfolioFixtures>({
  seededPortfolio: [
    async ({ userToken }, use) => {
      const ctx = await pwRequest.newContext();

      // Check existing portfolio to avoid duplicates
      let existing: string[] = [];
      try {
        const portfolio = await apiGetPortfolio(
          ctx,
          userToken,
        );
        existing = portfolio.holdings.map((h) => h.ticker);
      } catch {
        // Portfolio may not exist yet — that's fine.
      }

      // Seed missing stocks
      for (const stock of ALL_STOCKS) {
        if (existing.includes(stock.ticker)) continue;
        await apiAddPortfolioHolding(
          ctx,
          userToken,
          stock.ticker,
          stock.quantity,
          stock.price,
        );
      }

      await use(true);
      await ctx.dispose();
    },
    { scope: "test" },
  ],
});

export { expect } from "@playwright/test";
