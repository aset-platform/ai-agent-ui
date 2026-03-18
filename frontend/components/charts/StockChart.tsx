"use client";
/**
 * Multi-pane stock chart using TradingView lightweight-charts.
 *
 * Pane 1: Candlestick + SMA 50/200 + Bollinger Bands
 * Pane 2: Volume histogram
 * Pane 3: RSI (14) with 70/30 reference lines
 * Pane 4: MACD line + Signal + Histogram
 *
 * ~45 KB bundle vs ~8 MB for plotly.js.
 */

import {
  useRef,
  useEffect,
  useCallback,
  useState,
  type CSSProperties,
} from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type DeepPartial,
  type Time,
  type TimeChartOptions,
} from "lightweight-charts";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

export interface OHLCVRow {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IndicatorRow {
  date: string;
  sma_50: number | null;
  sma_200: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  bb_upper: number | null;
  bb_lower: number | null;
}

interface StockChartProps {
  ohlcv: OHLCVRow[];
  indicators: IndicatorRow[];
  isDark: boolean;
  height?: number;
}

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

/** Convert "YYYY-MM-DD" → lightweight-charts time string. */
function toTime(d: string) {
  return d as string;
}

function filterNull(
  arr: { time: string; value: number | null }[],
): { time: string; value: number }[] {
  return arr.filter(
    (p): p is { time: string; value: number } =>
      p.value != null && !Number.isNaN(p.value),
  );
}

// ---------------------------------------------------------------
// Component
// ---------------------------------------------------------------

export function StockChart({
  ohlcv,
  indicators,
  isDark,
  height = 700,
}: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // Track <html> dark class reactively via
  // MutationObserver so the chart always matches.
  const [domDark, setDomDark] = useState(() =>
    typeof document !== "undefined"
      ? document.documentElement.classList.contains(
          "dark",
        )
      : false,
  );
  useEffect(() => {
    const el = document.documentElement;
    const update = () =>
      setDomDark(el.classList.contains("dark"));
    update();
    const obs = new MutationObserver(update);
    obs.observe(el, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, []);

  // Use whichever source has resolved to dark.
  const actualDark = isDark || domDark;

  const bg = actualDark ? "#111827" : "#ffffff";
  const text = actualDark ? "#9ca3af" : "#6b7280";
  const grid = actualDark
    ? "rgba(55,65,81,0.3)"
    : "rgba(229,231,235,0.6)";

  const buildChart = useCallback(() => {
    const el = containerRef.current;
    if (!el || ohlcv.length === 0) return;

    // Cleanup previous chart
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const chartOptions: DeepPartial<TimeChartOptions> =
      {
        width: el.clientWidth,
        height,
        layout: {
          background: {
            type: ColorType.Solid,
            color: bg,
          },
          textColor: text,
          fontFamily:
            "'IBM Plex Mono', 'DM Sans', system-ui",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: grid },
          horzLines: { color: grid },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
        },
        rightPriceScale: {
          borderColor: grid,
          scaleMargins: {
            top: 0.05,
            bottom: 0.05,
          },
        },
        timeScale: {
          borderColor: grid,
          timeVisible: false,
          rightOffset: 5,
          barSpacing: 6,
        },
      };

    const chart = createChart(el, chartOptions);
    chartRef.current = chart;

    // ── Pane 1: Candlestick + overlays ──────────

    const candleSeries = chart.addSeries(
      CandlestickSeries,
      {
        upColor: "#10b981",
        downColor: "#ef4444",
        borderUpColor: "#10b981",
        borderDownColor: "#ef4444",
        wickUpColor: "#10b981",
        wickDownColor: "#ef4444",
      },
    );

    candleSeries.setData(
      ohlcv.map((d) => ({
        time: toTime(d.date),
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      })),
    );

    // SMA 50
    const sma50 = chart.addSeries(LineSeries, {
      color: "#f59e0b",
      lineWidth: 1,
      lineStyle: 2, // dashed
      priceLineVisible: false,
      lastValueVisible: false,
      title: "SMA 50",
    });
    sma50.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.sma_50,
        })),
      ),
    );

    // SMA 200
    const sma200 = chart.addSeries(LineSeries, {
      color: "#ef4444",
      lineWidth: 1,
      lineStyle: 3, // dotted
      priceLineVisible: false,
      lastValueVisible: false,
      title: "SMA 200",
    });
    sma200.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.sma_200,
        })),
      ),
    );

    // Bollinger upper
    const bbUpper = chart.addSeries(LineSeries, {
      color: actualDark
        ? "rgba(165,180,252,0.4)"
        : "rgba(99,102,241,0.3)",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "BB Upper",
    });
    bbUpper.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.bb_upper,
        })),
      ),
    );

    // Bollinger lower
    const bbLower = chart.addSeries(LineSeries, {
      color: actualDark
        ? "rgba(165,180,252,0.4)"
        : "rgba(99,102,241,0.3)",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "BB Lower",
    });
    bbLower.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.bb_lower,
        })),
      ),
    );

    // ── Pane 2: Volume ──────────────────────────

    const volumePane = chart.addPane();
    const volumeSeries = volumePane.addSeries(
      HistogramSeries,
      {
        priceLineVisible: false,
        lastValueVisible: false,
        title: "Volume",
        priceFormat: {
          type: "volume",
        },
      },
    );
    volumeSeries.setData(
      ohlcv.map((d) => ({
        time: toTime(d.date),
        value: d.volume,
        color:
          d.close >= d.open
            ? "rgba(16,185,129,0.4)"
            : "rgba(239,68,68,0.4)",
      })),
    );

    // ── Pane 3: RSI ─────────────────────────────

    const rsiPane = chart.addPane();
    const rsiSeries = rsiPane.addSeries(LineSeries, {
      color: "#8b5cf6",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "RSI 14",
    });
    rsiSeries.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.rsi_14,
        })),
      ),
    );

    // RSI reference lines (70 overbought, 30 oversold)
    rsiSeries.createPriceLine({
      price: 70,
      color: "rgba(251,191,36,0.5)",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: "Overbought",
    });
    rsiSeries.createPriceLine({
      price: 30,
      color: "rgba(251,191,36,0.5)",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: "Oversold",
    });

    // ── Pane 4: MACD ────────────────────────────

    const macdPane = chart.addPane();

    const macdLine = macdPane.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "MACD",
    });
    macdLine.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.macd,
        })),
      ),
    );

    const signalLine = macdPane.addSeries(
      LineSeries,
      {
        color: "#f59e0b",
        lineWidth: 2,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "Signal",
      },
    );
    signalLine.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.macd_signal,
        })),
      ),
    );

    const macdHist = macdPane.addSeries(
      HistogramSeries,
      {
        priceLineVisible: false,
        lastValueVisible: false,
        title: "Histogram",
      },
    );
    macdHist.setData(
      filterNull(
        indicators.map((d) => ({
          time: toTime(d.date),
          value: d.macd_hist,
        })),
      ).map((p) => ({
        ...p,
        color:
          p.value >= 0
            ? "rgba(16,185,129,0.6)"
            : "rgba(239,68,68,0.6)",
      })),
    );

    // ── Fit & default range ─────────────────────

    // Show last 6 months by default
    const sixMonthsAgo = new Date(
      Date.now() - 180 * 86400000,
    )
      .toISOString()
      .slice(0, 10);
    chart.timeScale().setVisibleRange({
      from: sixMonthsAgo as Time,
      to: ohlcv[ohlcv.length - 1].date as Time,
    });
  }, [ohlcv, indicators, actualDark, height, bg, text, grid]);

  // Build chart on mount / data change
  useEffect(() => {
    buildChart();
  }, [buildChart]);

  // Theme sync — apply colors without rebuilding
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.applyOptions({
      layout: {
        background: {
          type: ColorType.Solid,
          color: bg,
        },
        textColor: text,
      },
      grid: {
        vertLines: { color: grid },
        horzLines: { color: grid },
      },
      rightPriceScale: { borderColor: grid },
      timeScale: { borderColor: grid },
    });
  }, [actualDark, bg, text, grid]);

  // Resize handler
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const ro = new ResizeObserver(() => {
      chartRef.current?.applyOptions({
        width: el.clientWidth,
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Cleanup
  useEffect(() => {
    return () => {
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, []);

  const style: CSSProperties = {
    width: "100%",
    height,
    position: "relative",
  };

  return <div ref={containerRef} style={style} />;
}
