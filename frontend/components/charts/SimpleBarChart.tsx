"use client";
/**
 * Categorical bar chart using Apache ECharts (tree-shaken).
 *
 * Replaces PlotlyChart for simple `{ x: string[], y: number[] }`
 * cases. Tree-shakes to ~50 KB incremental (BarChart module on
 * top of `echarts/core`, which Dashboard widgets already load),
 * vs plotly.js-basic-dist at 1 MB.
 */

import { useMemo } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "@/hooks/useTheme";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  CanvasRenderer,
]);

const ReactECharts = dynamic(
  () => import("echarts-for-react"),
  {
    ssr: false,
    loading: () => (
      <div
        className="flex h-96 items-center justify-center
          rounded-lg bg-gray-100 animate-pulse
          dark:bg-gray-800"
      >
        <span className="text-sm text-gray-400">
          Loading chart...
        </span>
      </div>
    ),
  },
);

export interface BarSeries {
  name: string;
  /** Category-aligned y-values (same length as categories). */
  values: number[];
  /** Optional per-bar colour override (length === values). */
  colors?: string[];
}

interface SimpleBarChartProps {
  categories: string[];
  series: BarSeries[];
  title?: string;
  /** y-axis label (single-series convenience). */
  yAxisLabel?: string;
  height?: number;
  /** Group vs stack when multiple series. */
  stacked?: boolean;
  /** Show data labels above bars (single-series only by default). */
  showLabels?: boolean;
  /** Format a bar value for label/tooltip. */
  valueFormatter?: (v: number) => string;
  /** x-axis tick rotation in degrees. */
  tickRotation?: number;
}

const DEFAULT_COLORS = [
  "#6366f1",
  "#8b5cf6",
  "#ec4899",
  "#f59e0b",
  "#10b981",
  "#3b82f6",
  "#ef4444",
  "#06b6d4",
];

export function SimpleBarChart({
  categories,
  series,
  title,
  yAxisLabel,
  height = 384,
  stacked = false,
  showLabels = false,
  valueFormatter,
  tickRotation = -30,
}: SimpleBarChartProps) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const option = useMemo<any>(() => {
    const textColor = isDark ? "#e4e4e7" : "#374151";
    const gridColor = isDark
      ? "rgba(156,163,175,0.15)"
      : "#e5e7eb";

    const multi = series.length > 1;

    return {
      backgroundColor: "transparent",
      ...(title
        ? {
            title: {
              text: title,
              left: "center",
              top: 6,
              textStyle: {
                fontFamily:
                  "DM Sans, system-ui, sans-serif",
                fontSize: 14,
                fontWeight: 600,
                color: textColor,
              },
            },
          }
        : {}),
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: isDark
          ? "#27272a"
          : "#ffffff",
        borderColor: isDark ? "#3f3f46" : "#e4e4e7",
        textStyle: {
          fontFamily: "IBM Plex Mono, monospace",
          fontSize: 12,
          color: textColor,
        },
        ...(valueFormatter
          ? {
              valueFormatter: (v: number | string) =>
                valueFormatter(Number(v)),
            }
          : {}),
      },
      ...(multi
        ? {
            legend: {
              top: title ? 28 : 6,
              textStyle: {
                fontSize: 11,
                color: textColor,
              },
            },
          }
        : {}),
      grid: {
        left: 56,
        right: 24,
        top: title ? (multi ? 58 : 42) : multi ? 32 : 12,
        bottom: 56,
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: categories,
        axisLabel: {
          rotate: tickRotation,
          fontFamily: "IBM Plex Mono, monospace",
          fontSize: 11,
          color: isDark ? "#a1a1aa" : "#6b7280",
          interval: 0,
        },
        axisLine: {
          lineStyle: { color: gridColor },
        },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        name: yAxisLabel,
        nameTextStyle: {
          color: textColor,
          fontSize: 11,
        },
        axisLabel: {
          fontFamily: "IBM Plex Mono, monospace",
          fontSize: 11,
          color: isDark ? "#a1a1aa" : "#6b7280",
        },
        splitLine: {
          lineStyle: { color: gridColor },
        },
      },
      series: series.map((s, i) => ({
        name: s.name,
        type: "bar" as const,
        data: s.values.map((v, j) => ({
          value: v,
          itemStyle: s.colors?.[j]
            ? { color: s.colors[j] }
            : undefined,
        })),
        ...(stacked ? { stack: "total" } : {}),
        itemStyle: s.colors
          ? undefined
          : {
              color:
                DEFAULT_COLORS[i % DEFAULT_COLORS.length],
            },
        ...(showLabels && !multi
          ? {
              label: {
                show: true,
                position: "top" as const,
                fontFamily: "IBM Plex Mono, monospace",
                fontSize: 10,
                color: textColor,
                formatter: valueFormatter
                  ? (p: { value: number }) =>
                      valueFormatter(p.value)
                  : undefined,
              },
            }
          : {}),
      })),
    };
  }, [
    categories,
    series,
    title,
    yAxisLabel,
    valueFormatter,
    isDark,
    tickRotation,
    stacked,
    showLabels,
  ]);

  return (
    <div
      data-testid="simple-bar-chart"
      style={{ height }}
    >
      <ReactECharts
        echarts={echarts}
        option={option}
        style={{ height: "100%", width: "100%" }}
        notMerge
      />
    </div>
  );
}
