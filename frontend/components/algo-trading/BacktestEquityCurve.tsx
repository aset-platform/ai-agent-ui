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

  const option = useMemo(
    () => ({
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
    }),
    [points, initialCapitalInr],
  );

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
