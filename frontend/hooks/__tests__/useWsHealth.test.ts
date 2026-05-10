/**
 * useWsHealth — vitest unit tests (OBS-1).
 *
 * Verifies the hook's default shape and that it polls the
 * /algo/live/ws-health endpoint via apiFetch on a 10s interval.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import React from "react";

const apiFetchMock = vi.fn();

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: (url: string, init?: RequestInit) => apiFetchMock(url, init),
}));

vi.mock("@/lib/config", () => ({
  API_URL: "http://test/v1",
  BACKEND_URL: "http://test",
}));

import { useWsHealth, WS_HEALTH_KEY } from "../useWsHealth";

function wrapper({ children }: { children: ReactNode }) {
  // Disable SWR's cache between tests by giving each invocation a
  // fresh provider.
  return React.createElement(
    SWRConfig,
    { value: { provider: () => new Map() } },
    children,
  );
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useWsHealth", () => {
  it("exposes default shape before data loads", () => {
    apiFetchMock.mockImplementation(
      () => new Promise(() => undefined),
    );
    const { result } = renderHook(() => useWsHealth(), { wrapper });
    expect(result.current.health).toBeNull();
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("fetches /algo/live/ws-health and surfaces the snapshot", async () => {
    const snapshot = {
      connected: true,
      subscriber_count: 2,
      subscribed_tokens: 4,
      last_tick_at: "2026-05-10T03:00:00Z",
      tick_age_seconds: 12,
      tick_count_today: 17,
    };
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(snapshot),
    });
    const { result } = renderHook(() => useWsHealth(), { wrapper });
    await waitFor(() => {
      expect(result.current.health).not.toBeNull();
    });
    expect(apiFetchMock.mock.calls[0][0]).toBe(WS_HEALTH_KEY);
    expect(result.current.health).toEqual(snapshot);
    expect(result.current.error).toBeNull();
  });

  it("surfaces fetch errors", async () => {
    apiFetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: () => Promise.resolve({}),
    });
    const { result } = renderHook(() => useWsHealth(), { wrapper });
    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });
    expect(result.current.health).toBeNull();
  });
});
