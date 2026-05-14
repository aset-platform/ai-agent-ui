"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import type { EquityPoint } from "@/hooks/useBacktestRuns";

const ReactECharts = dynamic(
  () => import("echarts-for-react"),
  { ssr: false },
);

function useDarkMode(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const sync = () => {
      setDark(
        document.documentElement.classList.contains("dark"),
      );
    };
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, []);
  return dark;
}

interface Props {
  points: EquityPoint[];
  initialCapitalInr: string;
}

export function BacktestEquityCurve({
  points,
  initialCapitalInr,
}: Props) {
  const isDark = useDarkMode();

  const option = useMemo(() => {
    // ASETPLTFRM-400 slice 5 — intraday backtests emit ~25 points
    // per ``bar_date``. Plotting on a date-only category axis
    // collapses them into a single tick. When ANY point carries a
    // ``bar_open_ts_ns``, switch to a time-axis keyed off
    // ns-since-epoch so each intra-day bar has its own x position.
    const hasIntradayTs = points.some(
      (p) => typeof p.bar_open_ts_ns === "number",
    );
    if (hasIntradayTs) {
      const series = points.map((p) => {
        const ms =
          typeof p.bar_open_ts_ns === "number"
            ? p.bar_open_ts_ns / 1_000_000
            : new Date(p.bar_date).getTime();
        return [ms, Number(p.equity_inr)] as [number, number];
      });
      return {
        grid: { left: 50, right: 12, top: 16, bottom: 32 },
        xAxis: {
          type: "time" as const,
          axisLabel: { fontSize: 11 },
        },
        yAxis: {
          type: "value" as const,
          scale: true,
          axisLabel: { fontSize: 11 },
        },
        tooltip: { trigger: "axis" as const },
        series: [
          {
            type: "line" as const,
            showSymbol: false,
            lineStyle: { width: 2 },
            data: series,
            markLine: {
              symbol: "none",
              lineStyle: {
                type: "dashed" as const,
                color: "#94a3b8",
              },
              data: [{ yAxis: Number(initialCapitalInr) }],
            },
          },
        ],
      };
    }
    return {
      grid: { left: 50, right: 12, top: 16, bottom: 32 },
      xAxis: {
        type: "category" as const,
        data: points.map((p) => p.bar_date),
        axisLabel: { fontSize: 11 },
      },
      yAxis: {
        type: "value" as const,
        scale: true,
        axisLabel: { fontSize: 11 },
      },
      tooltip: { trigger: "axis" as const },
      series: [
        {
          type: "line" as const,
          showSymbol: false,
          lineStyle: { width: 2 },
          data: points.map((p) => Number(p.equity_inr)),
          markLine: {
            symbol: "none",
            lineStyle: {
              type: "dashed" as const,
              color: "#94a3b8",
            },
            data: [{ yAxis: Number(initialCapitalInr) }],
          },
        },
      ],
    };
  }, [points, initialCapitalInr]);

  if (points.length === 0) {
    return (
      <div
        className="flex h-64 items-center justify-center rounded-md border border-slate-200 dark:border-slate-700 text-sm text-slate-500"
        data-testid="backtest-equity-curve-empty"
      >
        No equity data yet
      </div>
    );
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-2"
      data-testid="backtest-equity-curve"
    >
      <ReactECharts
        option={option}
        notMerge={true}
        key={isDark ? "d" : "l"}
        style={{ height: 280, width: "100%" }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}
