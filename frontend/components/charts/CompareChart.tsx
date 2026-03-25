"use client";
/**
 * Compare stocks chart — TradingView lightweight-charts.
 *
 * One LineSeries per ticker, each with a distinct color.
 * Normalized prices (base = 100).
 */

import { useRef, useEffect } from "react";
import { useDomDark } from "./useDarkMode";
import {
  createChart,
  LineSeries,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type Time,
} from "lightweight-charts";

const COLORS = [
  "#6366f1", // indigo
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#f59e0b", // amber
  "#10b981", // emerald
  "#3b82f6", // blue
  "#ef4444", // red
  "#06b6d4", // cyan
];

export { COLORS as COMPARE_COLORS };

interface CompareSeries {
  ticker: string;
  dates: string[];
  normalized: number[];
}

interface CompareChartProps {
  series: CompareSeries[];
  isDark: boolean;
  height?: number;
}

export function CompareChart({
  series,
  isDark: isDarkProp,
  height = 400,
}: CompareChartProps) {
  const isDark = useDomDark(isDarkProp);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || series.length === 0) return;

    const bg = isDark ? "#111827" : "#ffffff";
    const text = isDark ? "#9ca3af" : "#6b7280";
    const grid = isDark
      ? "rgba(55,65,81,0.3)"
      : "rgba(229,231,235,0.5)";

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: {
          type: ColorType.Solid,
          color: bg,
        },
        textColor: text,
        fontFamily:
          "'IBM Plex Mono', monospace",
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
      },
      timeScale: {
        borderColor: grid,
        timeVisible: false,
      },
    });
    chartRef.current = chart;

    for (let i = 0; i < series.length; i++) {
      const s = series[i];
      const color =
        COLORS[i % COLORS.length];
      const lineSeries = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: 2,
          priceScaleId: "right",
          lastValueVisible: true,
          priceLineVisible: false,
          priceFormat: {
            type: "price",
            precision: 2,
            minMove: 0.01,
          },
        },
      );
      lineSeries.setData(
        s.dates.map((d, j) => ({
          time: d as Time,
          value: s.normalized[j],
        })),
      );
    }

    chart.timeScale().fitContent();

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({
          width: entry.contentRect.width,
        });
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [series, isDark, height]);

  return <div ref={containerRef} data-testid="compare-chart-canvas" />;
}
