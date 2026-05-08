import { describe, expect, it, vi, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

const replaceMock = vi.fn();
const searchParamsRef = { current: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParamsRef.current,
  useRouter: () => ({ replace: replaceMock }),
  usePathname: () => "/advanced-analytics/current-day-upmove",
}));

import { useFilterParams } from "../useFilterParams";

afterEach(() => {
  replaceMock.mockClear();
  searchParamsRef.current = new URLSearchParams();
});

describe("useFilterParams", () => {
  it("hydrates tech + fund from the URL", () => {
    searchParamsRef.current = new URLSearchParams(
      "?tech=golden_recent,price_gt_sma50&fund=fscore_ge_7",
    );
    const { result } = renderHook(() => useFilterParams());
    expect(result.current.tech).toEqual([
      "golden_recent",
      "price_gt_sma50",
    ]);
    expect(result.current.fund).toEqual(["fscore_ge_7"]);
  });

  it("drops unknown keys silently", () => {
    searchParamsRef.current = new URLSearchParams(
      "?tech=golden_recent,not_real&fund=garbage",
    );
    const { result } = renderHook(() => useFilterParams());
    expect(result.current.tech).toEqual(["golden_recent"]);
    expect(result.current.fund).toEqual([]);
  });

  it("setTech writes a sorted, comma-joined CSV to the URL", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useFilterParams());
    act(() => result.current.setTech(["price_gt_sma50", "golden_recent"]));
    act(() => vi.advanceTimersByTime(310));
    expect(replaceMock).toHaveBeenCalledTimes(1);
    const [url] = replaceMock.mock.calls[0];
    expect(url).toContain("tech=golden_recent%2Cprice_gt_sma50");
    vi.useRealTimers();
  });

  it("resetAll clears both bundle params from URL", async () => {
    vi.useFakeTimers();
    searchParamsRef.current = new URLSearchParams(
      "?tech=golden_recent&fund=fscore_ge_7&page=3",
    );
    const { result } = renderHook(() => useFilterParams());
    act(() => result.current.resetAll());
    act(() => vi.advanceTimersByTime(310));
    const [url] = replaceMock.mock.calls[0];
    expect(url).not.toContain("tech=");
    expect(url).not.toContain("fund=");
    expect(url).toContain("page=3");
    vi.useRealTimers();
  });
});
