import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, cleanup } from "@testing-library/react";

// jsdom has no ResizeObserver — stub it so chart effects can run.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).ResizeObserver =
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).ResizeObserver ?? ResizeObserverStub;

// Capture every createPriceLine + chart.remove call across the
// mocked candle series so we can assert on titles + counts.
const priceLineSpy = vi.fn();
const chartRemoveSpy = vi.fn();
const fakePriceLine = (cfg: { title?: string }) => ({
  options: () => cfg,
});

vi.mock("lightweight-charts", () => {
  const candleSeries = {
    setData: vi.fn(),
    createPriceLine: vi.fn((cfg) => {
      priceLineSpy(cfg);
      return fakePriceLine(cfg);
    }),
    removePriceLine: vi.fn(),
    priceScale: () => ({ applyOptions: vi.fn() }),
  };
  const lineSeries = {
    setData: vi.fn(),
    createPriceLine: vi.fn(() => ({ options: () => ({}) })),
    priceScale: () => ({ applyOptions: vi.fn() }),
  };
  const chart = {
    addSeries: vi.fn((kind) =>
      kind?.toString?.().includes?.("Candlestick")
        ? candleSeries
        : lineSeries,
    ),
    timeScale: () => ({
      applyOptions: vi.fn(),
      fitContent: vi.fn(),
      setVisibleRange: vi.fn(),
      subscribeVisibleLogicalRangeChange: vi.fn(),
    }),
    subscribeCrosshairMove: vi.fn(),
    remove: vi.fn(() => {
      chartRemoveSpy();
    }),
    applyOptions: vi.fn(),
  };
  return {
    createChart: vi.fn(() => chart),
    AreaSeries: "AreaSeries",
    CandlestickSeries: "CandlestickSeries",
    LineSeries: "LineSeries",
    HistogramSeries: "HistogramSeries",
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0 },
  };
});

import { StockChart } from "@/components/charts/StockChart";

const ohlcv = [
  { date: "2024-12-01", open: 100, high: 105,
    low:  95, close: 100, volume: 1_000_000 },
  { date: "2024-12-02", open: 100, high: 110,
    low:  98, close: 105, volume: 1_000_000 },
];
const indicators: never[] = [];

const baseProps = {
  ohlcv,
  indicators,
  isDark: false,
  height: 600,
  interval: "D" as const,
  onCrosshairMove: vi.fn(),
};

describe("StockChart S/R price lines", () => {
  beforeEach(() => {
    priceLineSpy.mockClear();
    chartRemoveSpy.mockClear();
    cleanup();
  });

  it("draws 3 supports + 3 resistances when toggle is on", () => {
    render(
      <StockChart
        {...baseProps}
        supportLevels={[80, 90, 95]}
        resistanceLevels={[120, 115, 110]}
        visibleIndicators={{
          sma50: false,
          sma200: false,
          bollinger: false,
          volume: false,
          rsi: false,
          rsi2: false,
          macd: false,
          supportResistance: true,
        }}
      />,
    );

    const titles = priceLineSpy.mock.calls.map(
      ([cfg]) => cfg.title,
    );
    const sr = titles
      .filter((t): t is string => /^[RS][123]$/.test(t ?? ""))
      .sort();
    expect(sr).toEqual(
      ["R1", "R2", "R3", "S1", "S2", "S3"],
    );
  });

  it("draws zero S/R lines when toggle is off", () => {
    render(
      <StockChart
        {...baseProps}
        supportLevels={[80, 90, 95]}
        resistanceLevels={[120, 115, 110]}
        visibleIndicators={{
          sma50: false,
          sma200: false,
          bollinger: false,
          volume: false,
          rsi: false,
          rsi2: false,
          macd: false,
          supportResistance: false,
        }}
      />,
    );

    const titles = priceLineSpy.mock.calls.map(
      ([cfg]) => cfg.title,
    );
    expect(
      titles.filter(
        (t) => /^[RS][123]$/.test(t ?? ""),
      ),
    ).toEqual([]);
  });

  it("disposes the chart on unmount", () => {
    const { unmount } = render(
      <StockChart
        {...baseProps}
        supportLevels={[80, 90, 95]}
        resistanceLevels={[120, 115, 110]}
        visibleIndicators={{
          sma50: false,
          sma200: false,
          bollinger: false,
          volume: false,
          rsi: false,
          rsi2: false,
          macd: false,
          supportResistance: true,
        }}
      />,
    );
    unmount();
    expect(chartRemoveSpy).toHaveBeenCalled();
  });
});
