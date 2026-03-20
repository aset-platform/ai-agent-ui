"use client";
/**
 * Portfolio performance chart — TradingView lightweight-charts.
 *
 * Pane 1 (70%): AreaSeries — portfolio value with gradient
 *               LineSeries — invested value (dashed gray)
 * Pane 2 (30%): HistogramSeries — daily P&L (green/red)
 */

import { useRef, useEffect, useCallback } from "react";
import { useDomDark } from "./useDarkMode";
import {
  createChart,
  AreaSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import type { PortfolioDailyPoint } from "@/lib/types";

interface PortfolioChartProps {
  data: PortfolioDailyPoint[];
  isDark: boolean;
  height?: number;
  onCrosshairMove?: (point: {
    date: string;
    value: number;
    invested_value: number;
    daily_pnl: number;
    daily_return_pct: number;
  } | null) => void;
}

export function PortfolioChart({
  data,
  isDark: isDarkProp,
  height = 500,
  onCrosshairMove,
}: PortfolioChartProps) {
  const isDark = useDomDark(isDarkProp);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // Build a lookup map for crosshair
  const dataMap = useRef(
    new Map<string, PortfolioDailyPoint>(),
  );
  useEffect(() => {
    const m = new Map<string, PortfolioDailyPoint>();
    for (const pt of data) {
      m.set(pt.date, pt);
    }
    dataMap.current = m;
  }, [data]);

  const handleCrosshair = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (param: any) => {
      if (!onCrosshairMove) return;
      if (!param.time) {
        onCrosshairMove(null);
        return;
      }
      const d = String(param.time);
      const pt = dataMap.current.get(d);
      if (pt) onCrosshairMove(pt);
    },
    [onCrosshairMove],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

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

    // Invested value — dashed gray line
    const investedSeries = chart.addSeries(
      LineSeries,
      {
        color: isDark ? "#f59e0b" : "#d97706",
        lineWidth: 2,
        lineStyle: 2, // Dashed
        priceScaleId: "right",
        crosshairMarkerVisible: false,
        lastValueVisible: false,
        priceLineVisible: false,
      },
    );
    investedSeries.setData(
      data.map((d) => ({
        time: d.date as Time,
        value: d.invested_value,
      })),
    );

    // Portfolio market value — area with gradient
    const valueSeries = chart.addSeries(
      AreaSeries,
      {
        lineColor: isDark
          ? "#818cf8"
          : "#6366f1",
        topColor: isDark
          ? "rgba(129,140,248,0.4)"
          : "rgba(99,102,241,0.3)",
        bottomColor: isDark
          ? "rgba(129,140,248,0.0)"
          : "rgba(99,102,241,0.0)",
        lineWidth: 2,
        priceScaleId: "right",
        priceFormat: {
          type: "price",
          precision: 2,
          minMove: 0.01,
        },
      },
    );
    valueSeries.setData(
      data.map((d) => ({
        time: d.date as Time,
        value: d.value,
      })),
    );

    // Daily P&L histogram
    const pnlSeries = chart.addSeries(
      HistogramSeries,
      {
        priceScaleId: "pnl",
        priceFormat: {
          type: "price",
          precision: 2,
          minMove: 0.01,
        },
      },
    );
    pnlSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
    pnlSeries.setData(
      data.map((d) => ({
        time: d.date as Time,
        value: d.daily_pnl,
        color:
          d.daily_pnl >= 0
            ? isDark
              ? "rgba(52,211,153,0.7)"
              : "rgba(16,185,129,0.7)"
            : isDark
              ? "rgba(248,113,113,0.7)"
              : "rgba(239,68,68,0.7)",
      })),
    );

    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove(handleCrosshair);

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
      chart.unsubscribeCrosshairMove(
        handleCrosshair,
      );
      chart.remove();
      chartRef.current = null;
    };
  }, [data, isDark, height, handleCrosshair]);

  return <div ref={containerRef} />;
}
