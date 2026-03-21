"use client";
/**
 * Stock forecast chart — TradingView lightweight-charts.
 *
 * Historical price (solid indigo) + forecast (dashed green)
 * + confidence band (green fill between upper/lower).
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

interface ForecastChartProps {
  historicalDates: string[];
  historicalPrices: number[];
  forecastDates: string[];
  forecastPredicted: number[];
  forecastUpper: number[];
  forecastLower: number[];
  isDark: boolean;
  height?: number;
  onCrosshairMove?: (info: {
    date: string;
    price: number;
    isForecast: boolean;
    lower?: number;
    upper?: number;
  } | null) => void;
}

export function ForecastChart({
  historicalDates,
  historicalPrices,
  forecastDates,
  forecastPredicted,
  forecastUpper,
  forecastLower,
  isDark: isDarkProp,
  height = 550,
  onCrosshairMove,
}: ForecastChartProps) {
  const isDark = useDomDark(isDarkProp);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // Lookup maps for crosshair
  const histMap = useRef(
    new Map<string, number>(),
  );
  const fcMap = useRef(
    new Map<
      string,
      { predicted: number; lower: number; upper: number }
    >(),
  );

  useEffect(() => {
    const hm = new Map<string, number>();
    for (let i = 0; i < historicalDates.length; i++) {
      hm.set(historicalDates[i], historicalPrices[i]);
    }
    histMap.current = hm;

    const fm = new Map<
      string,
      { predicted: number; lower: number; upper: number }
    >();
    for (let i = 0; i < forecastDates.length; i++) {
      fm.set(forecastDates[i], {
        predicted: forecastPredicted[i],
        lower: forecastLower[i],
        upper: forecastUpper[i],
      });
    }
    fcMap.current = fm;
  }, [
    historicalDates,
    historicalPrices,
    forecastDates,
    forecastPredicted,
    forecastLower,
    forecastUpper,
  ]);

  const handleCrosshair = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (param: any) => {
      if (!onCrosshairMove) return;
      if (!param.time) {
        onCrosshairMove(null);
        return;
      }
      const d = String(param.time);
      const hp = histMap.current.get(d);
      if (hp !== undefined) {
        onCrosshairMove({
          date: d,
          price: hp,
          isForecast: false,
        });
        return;
      }
      const fp = fcMap.current.get(d);
      if (fp) {
        onCrosshairMove({
          date: d,
          price: fp.predicted,
          isForecast: true,
          lower: fp.lower,
          upper: fp.upper,
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

    // Historical price (solid indigo)
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
      historicalDates.map((d, i) => ({
        time: d as Time,
        value: historicalPrices[i],
      })),
    );

    // Forecast section
    if (forecastDates.length > 0) {
      // Confidence upper (green fill)
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
        forecastDates.map((d, i) => ({
          time: d as Time,
          value: forecastUpper[i],
        })),
      );

      // Confidence lower (erase fill below)
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
        forecastDates.map((d, i) => ({
          time: d as Time,
          value: forecastLower[i],
        })),
      );

      // Forecast predicted (dashed green)
      const fcSeries = chart.addSeries(
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
      fcSeries.setData(
        forecastDates.map((d, i) => ({
          time: d as Time,
          value: forecastPredicted[i],
        })),
      );
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
    historicalDates,
    historicalPrices,
    forecastDates,
    forecastPredicted,
    forecastUpper,
    forecastLower,
    isDark,
    height,
    handleCrosshair,
  ]);

  return <div ref={containerRef} data-testid="forecast-chart-canvas" />;
}
