"use client";
/**
 * W3: Portfolio P&L trend area chart (ASETPLTFRM-289).
 * Uses existing /dashboard/portfolio/performance endpoint.
 */

import { useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "@/hooks/useTheme";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";
import { usePortfolioPerformance } from "@/hooks/useDashboardData";
import type { EChartsOption } from "@/lib/echarts";
import "@/lib/echarts";

interface StaleTickerChipProps {
  stale: {
    ticker: string;
    last_valid_close_date: string;
    days_stale: number;
  }[];
}

function StaleTickerChip({ stale }: StaleTickerChipProps) {
  const [open, setOpen] = useState(false);
  if (stale.length === 0) return null;
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        data-testid="pltrend-stale-chip"
        className="inline-flex items-center gap-1 rounded-md bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 px-2 py-0.5 text-xs font-medium border border-amber-200 dark:border-amber-800/50"
      >
        <svg
          className="w-3 h-3"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        {stale.length} holding
        {stale.length === 1 ? "" : "s"} using previous close
      </button>
      {open && (
        <div
          role="tooltip"
          data-testid="pltrend-stale-tooltip"
          className="absolute right-0 top-full mt-1 z-20 min-w-[240px] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg p-3 text-xs"
        >
          <p className="text-gray-700 dark:text-gray-200 font-medium mb-2">
            Showing yesterday&apos;s close for:
          </p>
          <ul className="space-y-1">
            {stale.map((s) => (
              <li
                key={s.ticker}
                className="flex items-center justify-between text-gray-600 dark:text-gray-300"
              >
                <span className="font-mono">
                  {s.ticker}
                </span>
                <span className="text-gray-400 dark:text-gray-500">
                  {s.last_valid_close_date} ·{" "}
                  {s.days_stale}d old
                </span>
              </li>
            ))}
          </ul>
          <p className="text-gray-400 dark:text-gray-500 mt-2 text-[11px]">
            Upstream data hasn&apos;t settled. Auto-clears
            on next refresh.
          </p>
        </div>
      )}
    </div>
  );
}

const ReactECharts = dynamic(
  () => import("echarts-for-react"),
  { ssr: false },
);

const PERIODS = ["1M", "3M", "6M", "1Y", "ALL"] as const;

interface Props {
  market: string;
}

export function PLTrendWidget({ market }: Props) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const [period, setPeriod] = useState<string>("ALL");

  // SWR refetches when period or market changes
  const data = usePortfolioPerformance(market, period);

  const pts = data.value?.data ?? [];
  const metrics = data.value?.metrics;
  const stale = data.value?.stale_tickers ?? [];

  const option = useMemo<EChartsOption>(() => {
    const dates = pts.map((p) => p.date);
    const values = pts.map((p) => p.value);
    const invested = pts.map(
      (p) => p.invested_value,
    );

    return {
      tooltip: {
        trigger: "axis",
        formatter: (raw: unknown) => {
          const params = (
            Array.isArray(raw) ? raw : [raw]
          ) as Record<string, unknown>[];
          const d = params[0];
          const inv = params[1];
          const date = d.axisValue as string;
          const val = (d.value as number).toLocaleString(
            undefined,
            { maximumFractionDigits: 0 },
          );
          const invVal = (
            inv.value as number
          ).toLocaleString(undefined, {
            maximumFractionDigits: 0,
          });
          return [
            `<b>${date}</b>`,
            `Portfolio: \u20b9${val}`,
            `Invested: \u20b9${invVal}`,
          ].join("<br/>");
        },
      },
      grid: {
        left: 60,
        right: 20,
        top: 10,
        bottom: 30,
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: {
          color: isDark ? "#a1a1aa" : "#71717a",
          fontSize: 10,
          rotate: 0,
          interval: Math.max(
            0, Math.floor(dates.length / 6) - 1,
          ),
        },
        boundaryGap: false,
      },
      yAxis: {
        type: "value",
        axisLabel: {
          color: isDark ? "#a1a1aa" : "#71717a",
          fontSize: 10,
          formatter: (v: number) =>
            `\u20b9${(v / 1000).toFixed(0)}k`,
        },
        splitLine: {
          lineStyle: {
            color: isDark ? "#3f3f46" : "#e4e4e7",
          },
        },
      },
      series: [
        {
          name: "Portfolio",
          type: "line",
          data: values,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: "#6366f1" },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(99,102,241,0.3)" },
                { offset: 1, color: "rgba(99,102,241,0.02)" },
              ],
            },
          },
        },
        {
          name: "Invested",
          type: "line",
          data: invested,
          smooth: true,
          showSymbol: false,
          lineStyle: {
            width: 1.5,
            color: "#71717a",
            type: "dashed",
          },
        },
      ],
    };
  }, [pts, isDark]);

  if (data.loading) return <WidgetSkeleton className="h-80" />;
  if (data.error) return <WidgetError message={data.error} />;

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Portfolio P&L Trend
          </h3>
          <StaleTickerChip stale={stale} />
        </div>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={[
                "px-2.5 py-1 text-xs rounded-md",
                "transition-colors",
                period === p
                  ? "bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 font-medium"
                  : "text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800",
              ].join(" ")}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {metrics && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-5 py-3 border-b border-gray-100 dark:border-gray-800">
          {[
            { label: "Total Return", value: `${metrics.total_return_pct?.toFixed(1)}%` },
            { label: "Sharpe Ratio", value: metrics.sharpe_ratio?.toFixed(2) ?? "—" },
            { label: "Max Drawdown", value: `${metrics.max_drawdown_pct?.toFixed(1)}%` },
            { label: "Annualized", value: `${metrics.annualized_return_pct?.toFixed(1)}%` },
          ].map((m) => (
            <div key={m.label} className="text-center">
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {m.label}
              </p>
              <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 font-mono">
                {m.value}
              </p>
            </div>
          ))}
        </div>
      )}

      <div className="px-3 py-2">
        {pts.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
            No performance data yet
          </p>
        ) : (
          <ReactECharts
            key={`${period}-${pts.length}`}
            option={option}
            notMerge={true}
            style={{ height: 220 }}
            opts={{ renderer: "canvas" }}
          />
        )}
      </div>
    </div>
  );
}
