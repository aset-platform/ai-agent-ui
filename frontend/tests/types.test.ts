/**
 * Compile-time + runtime validation for dashboard TypeScript
 * interfaces defined in frontend/lib/types.ts.
 *
 * Each test constructs a mock object matching the interface and
 * verifies key fields — if the file compiles, the shapes are correct.
 */

import { describe, it, expect } from "vitest";
import type {
  WatchlistResponse,
  ForecastsResponse,
  AnalysisResponse,
  LLMUsageResponse,
  ChatSessionSummary,
  TickerPrice,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// WatchlistResponse
// ---------------------------------------------------------------------------

describe("WatchlistResponse", () => {
  it("has correct shape with ticker data", () => {
    const mock: WatchlistResponse = {
      tickers: [
        {
          ticker: "AAPL",
          company_name: "Apple",
          current_price: 198,
          previous_close: 196,
          change: 2,
          change_pct: 1.02,
          sparkline: [190, 195, 198],
        },
      ],
      portfolio_value: 12000,
      daily_change: 200,
      daily_change_pct: 1.5,
    };
    expect(mock.tickers).toHaveLength(1);
    expect(mock.tickers[0].ticker).toBe("AAPL");
  });

  it("allows null for optional aggregate fields", () => {
    const mock: WatchlistResponse = {
      tickers: [],
      portfolio_value: null,
      daily_change: null,
      daily_change_pct: null,
    };
    expect(mock.portfolio_value).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// TickerPrice
// ---------------------------------------------------------------------------

describe("TickerPrice", () => {
  it("allows null company_name", () => {
    const mock: TickerPrice = {
      ticker: "XYZ",
      company_name: null,
      current_price: 50,
      previous_close: 49,
      change: 1,
      change_pct: 2.04,
      sparkline: [],
    };
    expect(mock.company_name).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ForecastsResponse
// ---------------------------------------------------------------------------

describe("ForecastsResponse", () => {
  it("has correct shape with forecast targets", () => {
    const mock: ForecastsResponse = {
      forecasts: [
        {
          ticker: "AAPL",
          run_date: "2026-03-15",
          current_price: 198,
          latest_close: 201.5,
          sentiment: "bullish",
          targets: [
            {
              horizon_months: 3,
              target_date: "2026-06-15",
              target_price: 215,
              pct_change: 8.5,
              lower_bound: 195,
              upper_bound: 235,
            },
          ],
          mae: 3.5,
          rmse: 4.1,
          mape: 2.3,
          confidence_score: 0.75,
          confidence_components: null,
        },
      ],
    };
    expect(mock.forecasts[0].targets).toHaveLength(1);
    expect(mock.forecasts[0].sentiment).toBe("bullish");
  });

  it("allows null for optional forecast fields", () => {
    const mock: ForecastsResponse = {
      forecasts: [
        {
          ticker: "TSLA",
          run_date: "2026-03-15",
          current_price: 180,
          latest_close: null,
          sentiment: null,
          targets: [],
          mae: null,
          rmse: null,
          mape: null,
          confidence_score: null,
          confidence_components: null,
        },
      ],
    };
    expect(mock.forecasts[0].sentiment).toBeNull();
    expect(mock.forecasts[0].mae).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AnalysisResponse
// ---------------------------------------------------------------------------

describe("AnalysisResponse", () => {
  it("has correct shape with signals", () => {
    const mock: AnalysisResponse = {
      analyses: [
        {
          ticker: "AAPL",
          analysis_date: "2026-03-15",
          signals: [
            {
              name: "RSI",
              value: "67",
              signal: "Neutral",
              description: "Approaching overbought",
            },
          ],
          sharpe_ratio: 1.45,
          annualized_return_pct: 18.5,
          annualized_volatility_pct: 22.3,
          max_drawdown_pct: -12.8,
        },
      ],
    };
    expect(mock.analyses[0].signals[0].signal).toBe("Neutral");
  });

  it("allows null for optional metric fields", () => {
    const mock: AnalysisResponse = {
      analyses: [
        {
          ticker: "XYZ",
          analysis_date: "2026-03-15",
          signals: [],
          sharpe_ratio: null,
          annualized_return_pct: null,
          annualized_volatility_pct: null,
          max_drawdown_pct: null,
        },
      ],
    };
    expect(mock.analyses[0].sharpe_ratio).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// LLMUsageResponse
// ---------------------------------------------------------------------------

describe("LLMUsageResponse", () => {
  it("has correct shape with models and daily trend", () => {
    const mock: LLMUsageResponse = {
      total_requests: 1234,
      total_cost_usd: 4.56,
      avg_latency_ms: 245,
      models: [
        {
          model: "llama",
          provider: "groq",
          request_count: 1000,
          total_tokens: 500000,
          estimated_cost_usd: 0.5,
        },
      ],
      daily_trend: [
        { date: "2026-03-15", requests: 100, cost: 0.5 },
      ],
    };
    expect(mock.total_requests).toBe(1234);
    expect(mock.models).toHaveLength(1);
    expect(mock.daily_trend).toHaveLength(1);
  });

  it("allows null avg_latency_ms", () => {
    const mock: LLMUsageResponse = {
      total_requests: 0,
      total_cost_usd: 0,
      avg_latency_ms: null,
      models: [],
      daily_trend: [],
    };
    expect(mock.avg_latency_ms).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ChatSessionSummary
// ---------------------------------------------------------------------------

describe("ChatSessionSummary", () => {
  it("has correct shape", () => {
    const mock: ChatSessionSummary = {
      session_id: "abc",
      started_at: "2026-03-15T10:00:00Z",
      ended_at: "2026-03-15T10:30:00Z",
      message_count: 5,
      preview: "Hello...",
      agent_ids_used: ["general"],
    };
    expect(mock.agent_ids_used).toContain("general");
    expect(mock.message_count).toBe(5);
  });

  it("supports multiple agent IDs", () => {
    const mock: ChatSessionSummary = {
      session_id: "xyz",
      started_at: "2026-03-15T11:00:00Z",
      ended_at: "2026-03-15T11:45:00Z",
      message_count: 12,
      preview: "Analyze AAPL...",
      agent_ids_used: ["general", "stock_analyst"],
    };
    expect(mock.agent_ids_used).toHaveLength(2);
  });
});
