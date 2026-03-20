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
  useMemo,
  useState,
  type CSSProperties,
} from "react";
import {
  createChart,
  AreaSeries,
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

export type ChartInterval = "D" | "W" | "M";

/**
 * Aggregate daily OHLCV rows into weekly or monthly
 * candles.  Daily rows are returned unchanged.
 */
export function aggregateOHLCV(
  rows: OHLCVRow[],
  interval: ChartInterval,
): OHLCVRow[] {
  if (interval === "D" || rows.length === 0)
    return rows;

  const bucketKey = (d: string): string => {
    const dt = new Date(d);
    if (interval === "M") {
      // Year-month bucket
      return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
    }
    // Weekly: ISO week — use Monday as bucket start
    const day = dt.getDay();
    const monday = new Date(dt);
    monday.setDate(
      dt.getDate() - ((day + 6) % 7),
    );
    return monday.toISOString().slice(0, 10);
  };

  const buckets = new Map<string, OHLCVRow[]>();
  for (const row of rows) {
    const key = bucketKey(row.date);
    let arr = buckets.get(key);
    if (!arr) {
      arr = [];
      buckets.set(key, arr);
    }
    arr.push(row);
  }

  const result: OHLCVRow[] = [];
  for (const [, bucket] of buckets) {
    result.push({
      date: bucket[0].date, // first date in bucket
      open: bucket[0].open,
      high: Math.max(...bucket.map((r) => r.high)),
      low: Math.min(...bucket.map((r) => r.low)),
      close: bucket[bucket.length - 1].close,
      volume: bucket.reduce(
        (sum, r) => sum + r.volume,
        0,
      ),
    });
  }
  return result;
}

/**
 * Aggregate daily indicator rows into weekly or monthly.
 * Uses the LAST value in each bucket (point-in-time snapshot).
 */
export function aggregateIndicators(
  rows: IndicatorRow[],
  interval: ChartInterval,
): IndicatorRow[] {
  if (interval === "D" || rows.length === 0)
    return rows;

  const bucketKey = (d: string): string => {
    const dt = new Date(d);
    if (interval === "M") {
      return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
    }
    const day = dt.getDay();
    const monday = new Date(dt);
    monday.setDate(
      dt.getDate() - ((day + 6) % 7),
    );
    return monday.toISOString().slice(0, 10);
  };

  const buckets = new Map<
    string,
    IndicatorRow[]
  >();
  for (const row of rows) {
    const key = bucketKey(row.date);
    let arr = buckets.get(key);
    if (!arr) {
      arr = [];
      buckets.set(key, arr);
    }
    arr.push(row);
  }

  const result: IndicatorRow[] = [];
  for (const [, bucket] of buckets) {
    // Use last row in bucket (latest snapshot)
    const last = bucket[bucket.length - 1];
    result.push({
      ...last,
      date: bucket[0].date, // align with OHLCV
    });
  }
  return result;
}

export interface IndicatorVisibility {
  sma50: boolean;
  sma200: boolean;
  bollinger: boolean;
  volume: boolean;
  rsi: boolean;
  macd: boolean;
}

export const DEFAULT_INDICATORS: IndicatorVisibility = {
  sma50: true,
  sma200: true,
  bollinger: false,
  volume: false,
  rsi: true,
  macd: true,
};

interface StockChartProps {
  ohlcv: OHLCVRow[];
  indicators: IndicatorRow[];
  isDark: boolean;
  height?: number;
  interval?: ChartInterval;
  visibleIndicators?: IndicatorVisibility;
  /** Called with OHLC + indicator data on crosshair. */
  onCrosshairMove?: (data: {
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    /** Overlay indicator values at crosshair. */
    overlays?: { name: string; value: number; color: string }[];
  } | null) => void;
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
  interval = "D",
  visibleIndicators = DEFAULT_INDICATORS,
  onCrosshairMove,
}: StockChartProps) {
  // Store callback in ref so it never triggers
  // chart rebuilds when the parent re-renders.
  const crosshairCbRef = useRef(onCrosshairMove);
  useEffect(() => {
    crosshairCbRef.current = onCrosshairMove;
  }, [onCrosshairMove]);

  // Memoize visibility by individual keys to avoid
  // object identity changes triggering rebuilds.
  const vis = useMemo(
    () => visibleIndicators,
    [
      visibleIndicators.sma50,
      visibleIndicators.sma200,
      visibleIndicators.bollinger,
      visibleIndicators.volume,
      visibleIndicators.rsi,
      visibleIndicators.macd,
    ],
  );

  // Aggregate both OHLCV and indicators by interval
  const aggOhlcv = useMemo(
    () => aggregateOHLCV(ohlcv, interval),
    [ohlcv, interval],
  );
  const aggIndicators = useMemo(
    () => aggregateIndicators(indicators, interval),
    [indicators, interval],
  );
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
    if (!el || aggOhlcv.length === 0) return;

    // Cleanup previous chart
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    // Re-check DOM at build time to catch any
    // stale closure values for actualDark.
    const liveDark =
      document.documentElement.classList.contains(
        "dark",
      );
    const bgLive = liveDark
      ? "#111827"
      : "#ffffff";
    const textLive = liveDark
      ? "#9ca3af"
      : "#6b7280";
    const gridLive = liveDark
      ? "rgba(55,65,81,0.3)"
      : "rgba(229,231,235,0.6)";

    const chartOptions: DeepPartial<TimeChartOptions> =
      {
        width: el.clientWidth,
        height,
        layout: {
          background: {
            type: ColorType.Solid,
            color: bgLive,
          },
          textColor: textLive,
          fontFamily:
            "'IBM Plex Mono', 'DM Sans', system-ui",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: gridLive },
          horzLines: { color: gridLive },
        },
        crosshair: {
          mode: CrosshairMode.Magnet,
        },
        rightPriceScale: {
          borderColor: gridLive,
          scaleMargins: {
            top: 0.05,
            bottom: 0.05,
          },
        },
        timeScale: {
          borderColor: gridLive,
          timeVisible: true,
          rightOffset: 5,
          barSpacing: 6,
          uniformDistribution: true,
        },
      };

    const chart = createChart(el, chartOptions);
    chartRef.current = chart;

    // ── Pane 1: Candlestick + overlays ──────────

    const candleData = aggOhlcv
      .filter(
        (d) =>
          d.open != null &&
          d.high != null &&
          d.low != null &&
          d.close != null,
      )
      .map((d) => ({
        time: toTime(d.date),
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      }));

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
    candleSeries.setData(candleData);

    // Track overlay series for crosshair tooltips
    type OverlaySeries = {
      name: string;
      color: string;
      series: ReturnType<typeof chart.addSeries>;
    };
    const overlaySeries: OverlaySeries[] = [];

    if (vis.sma50) {
      const sma50 = chart.addSeries(LineSeries, {
        color: "#f59e0b",
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "",
      });
      sma50.setData(
        filterNull(
          aggIndicators.map((d) => ({
            time: toTime(d.date),
            value: d.sma_50,
          })),
        ),
      );
      overlaySeries.push({
        name: "SMA 50",
        color: "#f59e0b",
        series: sma50,
      });
    }

    if (vis.sma200) {
      const sma200 = chart.addSeries(LineSeries, {
        color: "#ef4444",
        lineWidth: 1,
        lineStyle: 3,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "",
      });
      sma200.setData(
        filterNull(
          aggIndicators.map((d) => ({
            time: toTime(d.date),
            value: d.sma_200,
          })),
        ),
      );
      overlaySeries.push({
        name: "SMA 200",
        color: "#ef4444",
        series: sma200,
      });
    }

    if (vis.bollinger) {
      // Orange border + peach fill (like TradingView)
      const bbBorder = liveDark
        ? "#fb923c"
        : "#ea580c";
      const bbFill = liveDark
        ? "rgba(251,146,60,0.12)"
        : "rgba(234,88,12,0.10)";

      // Upper: fills down with peach color
      const bbUpper = chart.addSeries(AreaSeries, {
        lineColor: bbBorder,
        lineWidth: 1,
        topColor: bbFill,
        bottomColor: bbFill,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "",
      });
      bbUpper.setData(
        filterNull(
          aggIndicators.map((d) => ({
            time: toTime(d.date),
            value: d.bb_upper,
          })),
        ),
      );

      // Lower: fills down with OPAQUE background
      // to mask the upper fill below the lower line
      const bbLower = chart.addSeries(AreaSeries, {
        lineColor: bbBorder,
        lineWidth: 1,
        topColor: bgLive,
        bottomColor: bgLive,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "",
      });
      bbLower.setData(
        filterNull(
          aggIndicators.map((d) => ({
            time: toTime(d.date),
            value: d.bb_lower,
          })),
        ),
      );

      overlaySeries.push(
        {
          name: "BB Upper",
          color: bbBorder,
          series: bbUpper,
        },
        {
          name: "BB Lower",
          color: bbBorder,
          series: bbLower,
        },
      );
    }

    // Track sub-panes for stretch factor sizing
    const subPanes: ReturnType<
      typeof chart.addPane
    >[] = [];

    // ── Pane 2: Volume ──────────────────────────

    if (vis.volume) {
      const volumePane = chart.addPane();
      subPanes.push(volumePane);
      const volumeSeries = volumePane.addSeries(
        HistogramSeries,
        {
          priceLineVisible: false,
          lastValueVisible: false,
          title: "",
          priceFormat: { type: "volume" },
        },
      );
      volumeSeries.setData(
        aggOhlcv.map((d) => ({
          time: toTime(d.date),
          value: d.volume,
          color:
            d.close >= d.open
              ? "rgba(16,185,129,0.4)"
              : "rgba(239,68,68,0.4)",
        })),
      );
    }

    // ── Pane 3: RSI ─────────────────────────────

    if (vis.rsi) {
      const rsiPane = chart.addPane();
      subPanes.push(rsiPane);
      const rsiSeries = rsiPane.addSeries(
        LineSeries,
        {
          color: "#8b5cf6",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          title: "",
        },
      );
      rsiSeries.setData(
        filterNull(
          aggIndicators.map((d) => ({
            time: toTime(d.date),
            value: d.rsi_14,
          })),
        ),
      );
      rsiSeries.createPriceLine({
        price: 70,
        color: "rgba(251,191,36,0.5)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "",
      });
      rsiSeries.createPriceLine({
        price: 30,
        color: "rgba(251,191,36,0.5)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "",
      });
    }

    // ── Pane 4: MACD ────────────────────────────

    if (vis.macd) {
      const macdPane = chart.addPane();
      subPanes.push(macdPane);

      const macdLine = macdPane.addSeries(
        LineSeries,
        {
          color: "#3b82f6",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          title: "",
        },
      );
      macdLine.setData(
        filterNull(
          aggIndicators.map((d) => ({
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
        title: "",
      },
      );
      signalLine.setData(
        filterNull(
          aggIndicators.map((d) => ({
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
          title: "",
        },
      );
      macdHist.setData(
        filterNull(
          aggIndicators.map((d) => ({
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
    }

    // ── Pane sizing: price gets most space ───────

    // Price pane is pane index 0 (the default pane).
    // Give sub-panes a small fraction so price
    // always dominates, regardless of how many
    // indicators are visible.
    if (subPanes.length > 0) {
      const mainPane = chart.panes()[0];
      // Each sub-pane gets 0.15; price gets the rest
      const subFactor = 0.15;
      const mainFactor =
        1 - subPanes.length * subFactor;
      mainPane.setStretchFactor(mainFactor);
      for (const sp of subPanes) {
        sp.setStretchFactor(subFactor);
      }
    }

    // ── Crosshair → OHLC + overlay legend ───────

    if (crosshairCbRef.current) {
      chart.subscribeCrosshairMove((param) => {
        if (!param.time) {
          crosshairCbRef.current?.(null);
          return;
        }
        const cd = param.seriesData.get(
          candleSeries,
        ) as {
          open: number;
          high: number;
          low: number;
          close: number;
        } | undefined;
        if (!cd) return;

        const ts = String(param.time);
        const match = aggOhlcv.find(
          (r) => r.date === ts,
        );

        // Collect overlay values at this time
        const overlays: {
          name: string;
          value: number;
          color: string;
        }[] = [];
        for (const ov of overlaySeries) {
          const val = param.seriesData.get(
            ov.series,
          ) as { value: number } | undefined;
          if (val?.value != null) {
            overlays.push({
              name: ov.name,
              value: val.value,
              color: ov.color,
            });
          }
        }

        crosshairCbRef.current?.({
          date: ts,
          open: cd.open,
          high: cd.high,
          low: cd.low,
          close: cd.close,
          volume: match?.volume ?? 0,
          overlays,
        });
      });
    }

    // ── Fit & default range ─────────────────────

    // Default visible range per interval:
    // D = 6 months, W = 1 year, M = all data
    const lastDate = new Date(
      aggOhlcv[aggOhlcv.length - 1].date,
    );
    const rangeDays =
      interval === "W"
        ? 730
        : interval === "D"
          ? 180
          : 0;

    if (rangeDays > 0) {
      const from = new Date(
        lastDate.getTime() - rangeDays * 86400000,
      )
        .toISOString()
        .slice(0, 10);
      chart.timeScale().setVisibleRange({
        from: from as Time,
        to: aggOhlcv[
          aggOhlcv.length - 1
        ].date as Time,
      });
    } else {
      chart.timeScale().fitContent();
    }
  }, [aggOhlcv, aggIndicators, actualDark, height, interval, bg, text, grid, vis]);

  // Build chart on mount / data change
  useEffect(() => {
    buildChart();
  }, [buildChart]);

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
