/**
 * Unit tests for frontend/hooks/useDashboardData.ts
 *
 * Verifies that the dashboard data hooks are properly exported
 * and that the DashboardData interface shape is correct.
 */

import { describe, it, expect, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks — must precede any import that touches these modules
// ---------------------------------------------------------------------------

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));
vi.mock("@/lib/config", () => ({
  API_URL: "http://test:8181/v1",
}));

// ---------------------------------------------------------------------------
// Export smoke tests
// ---------------------------------------------------------------------------

describe("useDashboardData exports", () => {
  it("useWatchlist is exported as a function", async () => {
    const mod = await import("@/hooks/useDashboardData");
    expect(typeof mod.useWatchlist).toBe("function");
  });

  it("useForecastSummary is exported as a function", async () => {
    const mod = await import("@/hooks/useDashboardData");
    expect(typeof mod.useForecastSummary).toBe("function");
  });

  it("useAnalysisLatest is exported as a function", async () => {
    const mod = await import("@/hooks/useDashboardData");
    expect(typeof mod.useAnalysisLatest).toBe("function");
  });

  it("useLLMUsage is exported as a function", async () => {
    const mod = await import("@/hooks/useDashboardData");
    expect(typeof mod.useLLMUsage).toBe("function");
  });
});

// ---------------------------------------------------------------------------
// DashboardData interface shape
// ---------------------------------------------------------------------------

describe("DashboardData interface", () => {
  it("satisfies the expected shape at compile time", () => {
    // This test validates the TypeScript interface — if it
    // compiles, the shape is correct.
    type DI = import("@/hooks/useDashboardData").DashboardData<string>;
    const mock: DI = {
      value: "hello",
      loading: false,
      error: null,
    };
    expect(mock.value).toBe("hello");
    expect(mock.loading).toBe(false);
    expect(mock.error).toBeNull();
  });

  it("allows null value", () => {
    type DI = import("@/hooks/useDashboardData").DashboardData<number>;
    const mock: DI = {
      value: null,
      loading: true,
      error: null,
    };
    expect(mock.value).toBeNull();
    expect(mock.loading).toBe(true);
  });

  it("allows error string", () => {
    type DI = import("@/hooks/useDashboardData").DashboardData<number>;
    const mock: DI = {
      value: null,
      loading: false,
      error: "HTTP 500",
    };
    expect(mock.error).toBe("HTTP 500");
  });
});
