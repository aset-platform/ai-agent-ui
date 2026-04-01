"use client";
/**
 * Portfolio forecast chart — TradingView lightweight-charts.
 *
 * Historical: market value (solid indigo) + invested (dashed gray)
 * Forecast: predicted (dashed green) + confidence band + flat invested
 * Crosshair tooltip with gain/loss %
 */

import {
  useRef,
  useEffect,
  useCallback,
} from "react";
import { useDomDark } from "./useDarkMode";
import {
  createChart,
  AreaSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import type {
  PortfolioDailyPoint,
  PortfolioForecastPoint,
} from "@/lib/types";

interface PortfolioForecastChartProps {
  perfData: PortfolioDailyPoint[];
  forecastData: PortfolioForecastPoint[];
  isDark: boolean;
  height?: number;
  onCrosshairMove?: (info: {
    date: string;
    value: number;
    invested: number;
    gainPct: number;
    isForecast: boolean;
  } | null) => void;
}

export function PortfolioForecastChart({
  perfData,
  forecastData,
  isDark: isDarkProp,
  height = 480,
  onCrosshairMove,
}: PortfolioForecastChartProps) {
  const isDark = useDomDark(isDarkProp);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // Lookup maps for crosshair
  const perfMap = useRef(
    new Map<string, PortfolioDailyPoint>(),
  );
  const fcMap = useRef(
    new Map<string, PortfolioForecastPoint>(),
  );
  const lastInvestedRef = useRef(0);

  useEffect(() => {
    const pm = new Map<
      string,
      PortfolioDailyPoint
    >();
    for (const pt of perfData) pm.set(pt.date, pt);
    perfMap.current = pm;

    const fm = new Map<
      string,
      PortfolioForecastPoint
    >();
    for (const pt of forecastData) {
      fm.set(pt.date, pt);
    }
    fcMap.current = fm;

    lastInvestedRef.current =
      perfData.length > 0
        ? perfData[perfData.length - 1]
            .invested_value
        : 0;
  }, [perfData, forecastData]);

  const handleCrosshair = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (param: any) => {
      if (!onCrosshairMove) return;
      if (!param.time) {
        onCrosshairMove(null);
        return;
      }
      const d = String(param.time);
      const hp = perfMap.current.get(d);
      if (hp) {
        const inv = hp.invested_value;
        const gl =
          inv > 0
            ? ((hp.value - inv) / inv) * 100
            : 0;
        onCrosshairMove({
          date: d,
          value: hp.value,
          invested: inv,
          gainPct: gl,
          isForecast: false,
        });
        return;
      }
      const fp = fcMap.current.get(d);
      if (fp) {
        const inv = lastInvestedRef.current;
        const gl =
          inv > 0
            ? ((fp.predicted - inv) / inv) * 100
            : 0;
        onCrosshairMove({
          date: d,
          value: fp.predicted,
          invested: inv,
          gainPct: gl,
          isForecast: true,
        });
      }
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

    // --- Historical invested (dashed gray) ---
    const investedSeries = chart.addSeries(
      LineSeries,
      {
        color: isDark ? "#f59e0b" : "#d97706",
        lineWidth: 2,
        lineStyle: 2,
        priceScaleId: "right",
        crosshairMarkerVisible: false,
        lastValueVisible: false,
        priceLineVisible: false,
      },
    );
    investedSeries.setData(
      perfData.map((d) => ({
        time: d.date as Time,
        value: d.invested_value,
      })),
    );

    // --- Historical market value (solid indigo) ---
    const histSeries = chart.addSeries(
      LineSeries,
      {
        color: isDark ? "#818cf8" : "#6366f1",
        lineWidth: 2,
        priceScaleId: "right",
        priceFormat: {
          type: "price",
          precision: 2,
          minMove: 0.01,
        },
      },
    );
    histSeries.setData(
      perfData.map((d) => ({
        time: d.date as Time,
        value: d.value,
      })),
    );

    // --- Forecast section ---
    if (forecastData.length > 0) {
      // Confidence band upper (green fill)
      const upperSeries = chart.addSeries(
        AreaSeries,
        {
          lineColor: "transparent",
          topColor: isDark
            ? "rgba(52,211,153,0.15)"
            : "rgba(16,185,129,0.10)",
          bottomColor: "transparent",
          lineWidth: 1,
          priceScaleId: "right",
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        },
      );
      upperSeries.setData(
        forecastData.map((d) => ({
          time: d.date as Time,
          value: d.upper,
        })),
      );

      // Confidence band lower (erase fill)
      const lowerSeries = chart.addSeries(
        AreaSeries,
        {
          lineColor: "transparent",
          topColor: bg,
          bottomColor: "transparent",
          lineWidth: 1,
          priceScaleId: "right",
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        },
      );
      lowerSeries.setData(
        forecastData.map((d) => ({
          time: d.date as Time,
          value: d.lower,
        })),
      );

      // Forecast predicted (dashed green)
      const forecastSeries = chart.addSeries(
        LineSeries,
        {
          color: isDark ? "#34d399" : "#10b981",
          lineWidth: 2,
          lineStyle: 2,
          priceScaleId: "right",
          lastValueVisible: true,
          priceLineVisible: false,
        },
      );
      forecastSeries.setData(
        forecastData.map((d) => ({
          time: d.date as Time,
          value: d.predicted,
        })),
      );

      // Forecast invested (flat gray dashed)
      const lastInv =
        perfData.length > 0
          ? perfData[perfData.length - 1]
              .invested_value
          : 0;
      if (lastInv > 0) {
        const fcInvSeries = chart.addSeries(
          LineSeries,
          {
            color: isDark
              ? "#f59e0b"
              : "#d97706",
            lineWidth: 2,
            lineStyle: 2,
            priceScaleId: "right",
            crosshairMarkerVisible: false,
            lastValueVisible: false,
            priceLineVisible: false,
          },
        );
        fcInvSeries.setData(
          forecastData.map((d) => ({
            time: d.date as Time,
            value: lastInv,
          })),
        );
      }
    }

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
  }, [
    perfData,
    forecastData,
    isDark,
    height,
    handleCrosshair,
  ]);

  return <div ref={containerRef} data-testid="portfolio-forecast-chart-canvas" />;
}
