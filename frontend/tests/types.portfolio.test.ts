/**
 * Compile-time + runtime validation for portfolio TypeScript
 * interfaces defined in frontend/lib/types.ts.
 *
 * Each test constructs a mock object matching the interface
 * and verifies key fields — if the file compiles, the shapes
 * are correct.
 */

import { describe, it, expect } from "vitest";
import type {
  PortfolioDailyPoint,
  PortfolioMetrics,
  PortfolioPerformanceResponse,
  PortfolioForecastPoint,
  PortfolioForecastResponse,
} from "@/lib/types";

// -----------------------------------------------------------------
// PortfolioDailyPoint
// -----------------------------------------------------------------

describe("PortfolioDailyPoint", () => {
  it("has all required fields", () => {
    const point: PortfolioDailyPoint = {
      date: "2024-01-15",
      value: 1500.0,
      invested_value: 1400.0,
      daily_pnl: 100.0,
      daily_return_pct: 7.14,
    };
    expect(point.date).toBe("2024-01-15");
    expect(point.value).toBe(1500.0);
    expect(point.invested_value).toBe(1400.0);
    expect(point.daily_pnl).toBe(100.0);
    expect(point.daily_return_pct).toBe(7.14);
  });

  it("accepts zero values", () => {
    const point: PortfolioDailyPoint = {
      date: "2024-01-01",
      value: 0,
      invested_value: 0,
      daily_pnl: 0,
      daily_return_pct: 0,
    };
    expect(point.value).toBe(0);
  });
});

// -----------------------------------------------------------------
// PortfolioMetrics
// -----------------------------------------------------------------

describe("PortfolioMetrics", () => {
  it("has all required fields", () => {
    const m: PortfolioMetrics = {
      total_return_pct: 12.5,
      annualized_return_pct: 18.3,
      max_drawdown_pct: -8.2,
      sharpe_ratio: 1.45,
      best_day_pct: 4.1,
      best_day_date: "2024-03-10",
      worst_day_pct: -3.2,
      worst_day_date: "2024-02-15",
    };
    expect(m.total_return_pct).toBe(12.5);
    expect(m.sharpe_ratio).toBe(1.45);
    expect(m.best_day_date).toBe("2024-03-10");
  });

  it("allows null sharpe_ratio", () => {
    const m: PortfolioMetrics = {
      total_return_pct: 5.0,
      annualized_return_pct: 10.0,
      max_drawdown_pct: -2.0,
      sharpe_ratio: null,
      best_day_pct: 1.0,
      best_day_date: "2024-01-05",
      worst_day_pct: -1.0,
      worst_day_date: "2024-01-10",
    };
    expect(m.sharpe_ratio).toBeNull();
  });
});

// -----------------------------------------------------------------
// PortfolioPerformanceResponse
// -----------------------------------------------------------------

describe("PortfolioPerformanceResponse", () => {
  it("has correct shape with data and metrics", () => {
    const resp: PortfolioPerformanceResponse = {
      data: [
        {
          date: "2024-01-15",
          value: 1500.0,
          invested_value: 1400.0,
          daily_pnl: 100.0,
          daily_return_pct: 7.14,
        },
      ],
      metrics: {
        total_return_pct: 12.5,
        annualized_return_pct: 18.3,
        max_drawdown_pct: -8.2,
        sharpe_ratio: 1.45,
        best_day_pct: 4.1,
        best_day_date: "2024-03-10",
        worst_day_pct: -3.2,
        worst_day_date: "2024-02-15",
      },
      currency: "USD",
    };
    expect(resp.data).toHaveLength(1);
    expect(resp.metrics?.sharpe_ratio).toBe(1.45);
    expect(resp.currency).toBe("USD");
  });

  it("allows null metrics", () => {
    const resp: PortfolioPerformanceResponse = {
      data: [],
      metrics: null,
      currency: "INR",
    };
    expect(resp.metrics).toBeNull();
  });
});

// -----------------------------------------------------------------
// PortfolioForecastPoint
// -----------------------------------------------------------------

describe("PortfolioForecastPoint", () => {
  it("has all required fields", () => {
    const p: PortfolioForecastPoint = {
      date: "2024-06-15",
      predicted: 2000.0,
      lower: 1800.0,
      upper: 2200.0,
    };
    expect(p.date).toBe("2024-06-15");
    expect(p.predicted).toBe(2000.0);
    expect(p.lower).toBe(1800.0);
    expect(p.upper).toBe(2200.0);
  });
});

// -----------------------------------------------------------------
// PortfolioForecastResponse
// -----------------------------------------------------------------

describe("PortfolioForecastResponse", () => {
  it("has correct shape with forecast data", () => {
    const resp: PortfolioForecastResponse = {
      data: [
        {
          date: "2024-06-15",
          predicted: 2000.0,
          lower: 1800.0,
          upper: 2200.0,
        },
      ],
      horizon_months: 3,
      current_value: 1500.0,
      total_invested: 1400.0,
      currency: "USD",
    };
    expect(resp.data).toHaveLength(1);
    expect(resp.horizon_months).toBe(3);
    expect(resp.current_value).toBe(1500.0);
    expect(resp.currency).toBe("USD");
  });

  it("handles empty forecast data", () => {
    const resp: PortfolioForecastResponse = {
      data: [],
      horizon_months: 6,
      current_value: 0,
      total_invested: 0,
      currency: "INR",
    };
    expect(resp.data).toHaveLength(0);
  });
});
