// frontend/hooks/__tests__/useFactorScores.test.ts
/**
 * Regression test for the 200-ticker cap on GET /algo/factors
 * (backend/algo/routes/factors.py). Found 2026-07-03: a
 * superuser's full-registry watchlist (245 tickers) sent one
 * request over the cap and got a hard 400 with no partial data
 * ("Failed to load factor scores"). useFactorScores now chunks
 * into <=200-ticker requests internally and merges results — this
 * verifies that chunking + merging, not the React hook plumbing
 * (which mirrors the existing useKitePostbacks.test.ts pattern of
 * mocking SWR and capturing the fetcher it's given).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/lib/config", () => ({
  API_URL: "http://localhost:8181/v1",
}));

import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { useFactorScores } from "@/hooks/useFactorScores";

const mockUseSWR = useSWR as ReturnType<typeof vi.fn>;
const mockApiFetch = apiFetch as ReturnType<typeof vi.fn>;

function rowFor(ticker: string) {
  return { ticker, bar_date: "2026-07-03", sector: null, values: {} };
}

describe("useFactorScores — chunks requests over the 200-ticker cap", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("splits 245 tickers into two chunk requests and merges the results", async () => {
    let capturedFetcher: (() => Promise<unknown>) | undefined;
    mockUseSWR.mockImplementation((_key, fetcher) => {
      capturedFetcher = fetcher;
      return { data: undefined, error: undefined, isLoading: true, mutate: vi.fn() };
    });

    const tickers = Array.from({ length: 245 }, (_, i) => `T${String(i).padStart(4, "0")}.NS`);

    mockApiFetch.mockImplementation(async (url: string) => {
      const qs = new URL(url).searchParams.get("tickers")!;
      const chunkTickers = qs.split(",");
      return {
        ok: true,
        json: async () => chunkTickers.map(rowFor),
      };
    });

    renderHook(() => useFactorScores(tickers));
    const rows = (await capturedFetcher!()) as { ticker: string }[];

    // 245 tickers over a 200 cap -> exactly 2 requests.
    expect(mockApiFetch).toHaveBeenCalledTimes(2);
    const firstUrl = mockApiFetch.mock.calls[0][0] as string;
    const secondUrl = mockApiFetch.mock.calls[1][0] as string;
    expect(new URL(firstUrl).searchParams.get("tickers")!.split(",").length).toBe(200);
    expect(new URL(secondUrl).searchParams.get("tickers")!.split(",").length).toBe(45);

    // Merged result covers all 245 tickers.
    expect(rows.length).toBe(245);
  });

  it("makes a single request when under the 200-ticker cap", async () => {
    let capturedFetcher: (() => Promise<unknown>) | undefined;
    mockUseSWR.mockImplementation((_key, fetcher) => {
      capturedFetcher = fetcher;
      return { data: undefined, error: undefined, isLoading: true, mutate: vi.fn() };
    });

    const tickers = Array.from({ length: 50 }, (_, i) => `T${String(i).padStart(4, "0")}.NS`);

    mockApiFetch.mockResolvedValue({
      ok: true,
      json: async () => tickers.map(rowFor),
    });

    renderHook(() => useFactorScores(tickers));
    await capturedFetcher!();

    expect(mockApiFetch).toHaveBeenCalledTimes(1);
  });
});
