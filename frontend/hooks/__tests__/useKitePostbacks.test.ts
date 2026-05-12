// frontend/hooks/__tests__/useKitePostbacks.test.ts
/**
 * Unit tests for useKitePostbacks.
 * SWR is mocked so tests never hit the network.
 */
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

// Mock SWR before importing the hook.
vi.mock("swr", () => ({
  default: vi.fn(),
}));

// Mock apiFetch to prevent real HTTP calls.
vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

// Mock config to give a deterministic API_URL.
vi.mock("@/lib/config", () => ({
  API_URL: "http://localhost:8181/v1",
}));

import useSWR from "swr";
import { useKitePostbacks } from "@/hooks/useKitePostbacks";

const mockUseSWR = useSWR as ReturnType<typeof vi.fn>;

const SAMPLE_POSTBACK = {
  event_ts: "2026-05-10T09:30:00Z",
  tradingsymbol: "RELIANCE.NS",
  status: "COMPLETE",
  filled_quantity: 5,
  average_price: 2950.75,
  raw: {
    order_id: "240510000111111",
    guid: "abc-123",
    status: "COMPLETE",
    tradingsymbol: "RELIANCE.NS",
    filled_quantity: 5,
    average_price: 2950.75,
    checksum: "deadbeef",
  },
};

describe("useKitePostbacks", () => {
  it("returns empty array and isLoading=true while fetching", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
      mutate: vi.fn(),
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.postbacks).toEqual([]);
    expect(result.current.isLoading).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("returns populated array when data is available", () => {
    mockUseSWR.mockReturnValue({
      data: [SAMPLE_POSTBACK],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.postbacks).toHaveLength(1);
    expect(result.current.postbacks[0].tradingsymbol).toBe(
      "RELIANCE.NS",
    );
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("returns error string when SWR errors", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: new Error("HTTP 500"),
      isLoading: false,
      mutate: vi.fn(),
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.postbacks).toEqual([]);
    expect(result.current.error).toBe("HTTP 500");
  });

  it("uses the correct SWR key (today + real-money filters)", () => {
    // ASETPLTFRM-382 — the hook now scopes to today (IST) and
    // real-money only so dry-run / prior-session rows don't bleed
    // onto the Live page. The exact `since_date` value depends on
    // wall-clock at test time; we match the structural shape and
    // assert the two new query params are present.
    mockUseSWR.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });

    renderHook(() => useKitePostbacks());

    const [key] = mockUseSWR.mock.calls[0];
    expect(key).toMatch(
      /^http:\/\/localhost:8181\/v1\/algo\/live\/postbacks\?/,
    );
    expect(key).toContain("limit=50");
    expect(key).toContain("dry_run=false");
    expect(key).toMatch(/since_date=\d{4}-\d{2}-\d{2}/);
  });

  it("passes revalidateOnFocus: false to SWR", () => {
    mockUseSWR.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });

    renderHook(() => useKitePostbacks());

    const [, , opts] = mockUseSWR.mock.calls[0];
    expect(opts.revalidateOnFocus).toBe(false);
    expect(opts.refreshInterval).toBe(30_000);
  });

  it("exposes mutate for manual revalidation", () => {
    const mockMutate = vi.fn();
    mockUseSWR.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate: mockMutate,
    });

    const { result } = renderHook(() => useKitePostbacks());

    expect(result.current.mutate).toBe(mockMutate);
  });
});
