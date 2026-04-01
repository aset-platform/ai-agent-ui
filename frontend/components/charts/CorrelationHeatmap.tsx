"use client";
/**
 * Correlation heatmap using Apache ECharts.
 *
 * Tree-shakes to only import the heatmap module
 * (~150 KB vs 800 KB for full ECharts).
 */

import { useMemo } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "@/hooks/useTheme";

// Tree-shake: import only what we need
import * as echarts from "echarts/core";
import {
  HeatmapChart,
  type HeatmapSeriesOption,
} from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  type GridComponentOption,
  type TooltipComponentOption,
  type VisualMapComponentOption,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

type EChartsOption = echarts.ComposeOption<
  | HeatmapSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | VisualMapComponentOption
>;

// Dynamic import for SSR safety
const ReactECharts = dynamic(
  () => import("echarts-for-react"),
  {
    ssr: false,
    loading: () => (
      <div
        className="flex h-64 items-center
          justify-center rounded-lg bg-gray-100
          animate-pulse dark:bg-gray-800"
      >
        <span className="text-sm text-gray-400">
          Loading chart...
        </span>
      </div>
    ),
  },
);

interface CorrelationHeatmapProps {
  tickers: string[];
  matrix: number[][];
  title?: string;
  height?: number;
}

export function CorrelationHeatmap({
  tickers,
  matrix,
  title = "Portfolio Correlation",
  height,
}: CorrelationHeatmapProps) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  const chartHeight = height ?? Math.max(
    450, tickers.length * 55 + 140,
  );

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const option = useMemo<any>(() => {
    // Build [x, y, value] data array
    const data: [number, number, number][] = [];
    for (let i = 0; i < tickers.length; i++) {
      for (let j = 0; j < tickers.length; j++) {
        const v = matrix[i]?.[j] ?? 0;
        data.push([j, i, parseFloat(v.toFixed(2))]);
      }
    }

    return {
      backgroundColor: "transparent",
      title: {
        text: title,
        left: "center",
        top: 8,
        textStyle: {
          fontFamily: "DM Sans, sans-serif",
          fontSize: 15,
          fontWeight: 600,
          color: isDark ? "#e4e4e7" : "#18181b",
        },
      },
      tooltip: {
        trigger: "item",
        backgroundColor: isDark
          ? "#27272a"
          : "#ffffff",
        borderColor: isDark
          ? "#3f3f46"
          : "#e4e4e7",
        borderWidth: 1,
        textStyle: {
          fontFamily:
            "IBM Plex Mono, monospace",
          fontSize: 12,
          color: isDark ? "#e4e4e7" : "#18181b",
        },
        formatter: (
          params: {
            data: [number, number, number];
          },
        ) => {
          const [x, y, v] = (
            params as {
              data: [number, number, number];
            }
          ).data;
          return (
            `<strong>${tickers[y]}</strong>`
            + ` vs `
            + `<strong>${tickers[x]}</strong>`
            + `<br/>Correlation: <b>${v.toFixed(2)}</b>`
          );
        },
      },
      grid: {
        left: 110,
        right: 80,
        top: 50,
        bottom: 100,
        containLabel: false,
      },
      xAxis: {
        type: "category",
        data: tickers,
        splitArea: { show: true },
        axisLabel: {
          fontFamily:
            "IBM Plex Mono, monospace",
          fontSize: 11,
          color: isDark ? "#a1a1aa" : "#71717a",
          rotate: 45,
          interval: 0,
        },
        axisLine: {
          lineStyle: {
            color: isDark
              ? "#3f3f46"
              : "#e4e4e7",
          },
        },
        axisTick: { show: false },
      },
      yAxis: {
        type: "category",
        data: tickers,
        splitArea: { show: true },
        axisLabel: {
          fontFamily:
            "IBM Plex Mono, monospace",
          fontSize: 11,
          color: isDark ? "#a1a1aa" : "#71717a",
          interval: 0,
        },
        axisLine: {
          lineStyle: {
            color: isDark
              ? "#3f3f46"
              : "#e4e4e7",
          },
        },
        axisTick: { show: false },
      },
      visualMap: {
        min: -1,
        max: 1,
        calculable: false,
        orient: "vertical",
        right: 10,
        top: "center",
        itemHeight: 200,
        itemWidth: 14,
        text: ["+1.0", "-1.0"],
        textStyle: {
          fontFamily:
            "IBM Plex Mono, monospace",
          fontSize: 10,
          color: isDark ? "#a1a1aa" : "#71717a",
        },
        inRange: {
          color: [
            "#dc2626",  // -1  red
            "#f87171",  // -0.5
            isDark ? "#27272a" : "#f9fafb",
            "#60a5fa",  // +0.5
            "#2563eb",  // +1  blue
          ],
        },
      },
      series: [
        {
          type: "heatmap",
          data,
          label: {
            show: true,
            fontFamily:
              "IBM Plex Mono, monospace",
            fontSize: tickers.length > 10
              ? 9 : tickers.length > 6 ? 10 : 12,
            color: isDark ? "#e4e4e7" : "#18181b",
            formatter: (
              params: {
                data: [number, number, number];
              },
            ) => {
              const v = (
                params as {
                  data: [number, number, number];
                }
              ).data[2];
              return v.toFixed(2);
            },
          },
          emphasis: {
            itemStyle: {
              borderColor: isDark
                ? "#a1a1aa"
                : "#374151",
              borderWidth: 2,
              shadowBlur: 10,
              shadowColor: "rgba(0,0,0,0.2)",
            },
          },
          itemStyle: {
            borderColor: isDark
              ? "#18181b"
              : "#ffffff",
            borderWidth: 2,
            borderRadius: 3,
          },
        },
      ],
    };
  }, [tickers, matrix, title, isDark]);

  return (
    <div data-testid="correlation-heatmap">
      <ReactECharts
        echarts={echarts}
        option={option}
        style={{ height: chartHeight, width: "100%" }}
        notMerge
      />
    </div>
  );
}
