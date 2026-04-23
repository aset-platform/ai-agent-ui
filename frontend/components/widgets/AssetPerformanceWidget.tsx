"use client";
/**
 * W2: Asset-wise performance horizontal bar chart
 * (ASETPLTFRM-288). Uses existing portfolio holdings data.
 */

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";
import type { EChartsOption } from "@/lib/echarts";
import "@/lib/echarts";

const ReactECharts = dynamic(
  () => import("echarts-for-react"),
  { ssr: false },
);

/** Observe the `dark` class on <html> via MutationObserver. */
function useDarkMode(): boolean {
  const [dark, setDark] = useState(false);

  const sync = useCallback(() => {
    setDark(
      document.documentElement.classList.contains(
        "dark",
      ),
    );
  }, []);

  useEffect(() => {
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, [sync]);

  return dark;
}

interface Holding {
  ticker: string;
  gain_loss_pct: number;
}

interface Props {
  holdings: Holding[];
  loading: boolean;
  error: string | null;
}

export function AssetPerformanceWidget({
  holdings,
  loading,
  error,
}: Props) {
  const isDark = useDarkMode();

  // Row height per ticker and visible rows in the
  // fixed-height body; together they determine how
  // many bars fit without scrolling.
  const ROW_H = 28;
  const VISIBLE_ROWS = 9;
  const CHART_PAD = 40; // top + bottom grid padding

  const option: EChartsOption = (() => {
    // Show every holding sorted best-to-worst; no
    // top/bottom truncation — scrolling handles
    // overflow.
    const sorted = [...holdings].sort(
      (a, b) => b.gain_loss_pct - a.gain_loss_pct,
    );

    const tickers = sorted.map((h) => h.ticker);
    const values = sorted.map((h) =>
      Math.round(h.gain_loss_pct * 100) / 100,
    );

    return {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (p: Record<string, unknown>[]) => {
          const d = p[0];
          return `${d.name}: ${(d.value as number).toFixed(2)}%`;
        },
      },
      grid: {
        left: 100,
        right: 24,
        top: 10,
        bottom: 24,
      },
      xAxis: {
        type: "value",
        axisLabel: {
          formatter: "{value}%",
          color: isDark ? "#d4d4d8" : "#6b7280",
          fontSize: 11,
        },
        splitLine: {
          lineStyle: {
            color: isDark ? "#27272a" : "#f3f4f6",
            type: isDark ? "solid" : "dashed",
            width: isDark ? 0.5 : 1,
          },
        },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: "category",
        data: tickers,
        inverse: true,
        axisLabel: {
          color: isDark ? "#d4d4d8" : "#374151",
          fontSize: 12,
          fontWeight: 500,
        },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: "bar",
          data: values.map((v) => ({
            value: v,
            itemStyle: {
              color: v >= 0 ? "#22c55e" : "#ef4444",
              borderRadius: v >= 0
                ? [0, 4, 4, 0]
                : [4, 0, 0, 4],
            },
          })),
          barWidth: "60%",
        },
      ],
    };
  })();

  if (loading) return <WidgetSkeleton className="h-72" />;
  if (error) return <WidgetError message={error} />;

  const bodyMaxH = VISIBLE_ROWS * ROW_H + CHART_PAD;
  const chartH = Math.max(
    180,
    holdings.length * ROW_H + CHART_PAD,
  );
  const overflowing = holdings.length > VISIBLE_ROWS;

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm flex flex-col">
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800 flex items-baseline justify-between">
        <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Asset Performance
        </h3>
        {overflowing && (
          <span className="text-[11px] text-gray-400 dark:text-gray-500">
            {holdings.length} assets · scroll
          </span>
        )}
      </div>
      <div
        className="px-3 py-2 overflow-y-auto"
        style={{ maxHeight: bodyMaxH }}
      >
        {holdings.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
            No holdings to display
          </p>
        ) : (
          <ReactECharts
            key={isDark ? "dark" : "light"}
            option={option}
            notMerge={true}
            style={{ height: chartH }}
            opts={{ renderer: "canvas" }}
          />
        )}
      </div>
    </div>
  );
}
