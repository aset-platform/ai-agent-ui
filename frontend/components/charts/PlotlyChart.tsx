"use client";
/**
 * Lazy-loaded Plotly chart wrapper with SSR safety
 * and automatic dark/light theme integration.
 *
 * Usage:
 * ```tsx
 * <PlotlyChart
 *   data={[{ x: [1,2,3], y: [4,5,6], type: "scatter" }]}
 *   layout={{ title: "My Chart" }}
 * />
 * ```
 */

import dynamic from "next/dynamic";
import { useMemo } from "react";
import { useTheme } from "@/hooks/useTheme";

// Dynamic import with SSR disabled — plotly.js
// requires window/document which don't exist on server.
const Plot = dynamic(
  () => import("react-plotly.js"),
  {
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center h-64 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse">
        <span className="text-sm text-gray-400">
          Loading chart...
        </span>
      </div>
    ),
  },
);

/** Light mode chart colors. */
const LIGHT_THEME = {
  bg: "transparent",
  paper: "transparent",
  text: "#374151",
  grid: "#e5e7eb",
  line: "#6366f1",
};

/** Dark mode chart colors. */
const DARK_THEME = {
  bg: "transparent",
  paper: "transparent",
  text: "#9ca3af",
  grid: "#374151",
  line: "#818cf8",
};

/** Default color sequence for multi-series. */
export const CHART_COLORS = [
  "#6366f1", // indigo
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#f59e0b", // amber
  "#10b981", // emerald
  "#3b82f6", // blue
  "#ef4444", // red
  "#06b6d4", // cyan
];

interface PlotlyChartProps {
  data: Plotly.Data[];
  layout?: Partial<Plotly.Layout>;
  config?: Partial<Plotly.Config>;
  className?: string;
  height?: number;
}

export function PlotlyChart({
  data,
  layout = {},
  config = {},
  className = "",
  height = 300,
}: PlotlyChartProps) {
  const { resolvedTheme } = useTheme();
  const theme =
    resolvedTheme === "dark" ? DARK_THEME : LIGHT_THEME;

  const mergedLayout = useMemo<Partial<Plotly.Layout>>(
    () => ({
      height,
      margin: { t: 30, r: 20, b: 40, l: 50 },
      plot_bgcolor: theme.bg,
      paper_bgcolor: theme.paper,
      font: {
        color: theme.text,
        family: "'DM Sans', system-ui, sans-serif",
        size: 12,
      },
      xaxis: {
        gridcolor: theme.grid,
        zerolinecolor: theme.grid,
        ...(layout.xaxis as object),
      },
      yaxis: {
        gridcolor: theme.grid,
        zerolinecolor: theme.grid,
        ...(layout.yaxis as object),
      },
      colorway: CHART_COLORS,
      showlegend: true,
      legend: {
        font: { size: 11, color: theme.text },
        bgcolor: "transparent",
      },
      ...layout,
    }),
    [layout, theme, height],
  );

  const mergedConfig = useMemo<Partial<Plotly.Config>>(
    () => ({
      responsive: true,
      displayModeBar: false,
      ...config,
    }),
    [config],
  );

  return (
    <div className={`w-full ${className}`}>
      <Plot
        data={data}
        layout={mergedLayout}
        config={mergedConfig}
        useResizeHandler
        style={{ width: "100%", height }}
      />
    </div>
  );
}
