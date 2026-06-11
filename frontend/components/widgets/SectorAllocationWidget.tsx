"use client";
/**
 * W1: Sector allocation donut chart (ASETPLTFRM-287).
 */

import { useMemo } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "@/hooks/useTheme";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";
import type { DashboardData } from "@/hooks/useDashboardData";
import type { AllocationResponse } from "@/lib/types";
import type { EChartsOption } from "@/lib/echarts";
import "@/lib/echarts";

const ReactECharts = dynamic(
  () => import("echarts-for-react"),
  { ssr: false },
);

const COLORS = [
  "#6366f1", "#3b82f6", "#8b5cf6", "#06b6d4",
  "#10b981", "#f59e0b", "#ef4444", "#ec4899",
];

/** Live-overlay status — drives the source chip. Omitting it (or
 *  total_count 0) renders no chip. Same shape as Hero's meta. */
export interface SectorLiveMeta {
  live_count: number;
  eod_count: number;
  unknown_count: number;
  total_count: number;
}

interface Props {
  data: DashboardData<AllocationResponse>;
  liveMeta?: SectorLiveMeta;
}

export function SectorAllocationWidget({ data, liveMeta }: Props) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  const option = useMemo<EChartsOption>(() => {
    const sectors = data.value?.sectors ?? [];
    const total = data.value?.total_value ?? 0;
    const currency = data.value?.currency ?? "INR";
    const symbol = currency === "INR" ? "\u20b9" : "$";

    return {
      tooltip: {
        trigger: "item",
        formatter: (raw: unknown) => {
          const p = raw as Record<string, unknown>;
          const d = p.data as {
            name: string;
            value: number;
            stockCount: number;
          };
          return [
            `<b>${d.name}</b>`,
            `${symbol}${d.value.toLocaleString()}`,
            `${(p.percent as number)?.toFixed(1)}%`,
            `${d.stockCount} stock(s)`,
          ].join("<br/>");
        },
      },
      legend: {
        bottom: 0,
        textStyle: {
          color: isDark ? "#a1a1aa" : "#71717a",
          fontSize: 11,
        },
      },
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "45%"],
          avoidLabelOverlap: true,
          label: {
            show: true,
            position: "center",
            formatter: `${symbol}${total.toLocaleString()}`,
            fontSize: 16,
            fontWeight: "bold",
            color: isDark ? "#e4e4e7" : "#18181b",
          },
          emphasis: {
            label: { show: true },
          },
          data: sectors.map((s, i) => ({
            name: s.sector,
            value: s.value,
            stockCount: s.stock_count,
            itemStyle: {
              color: COLORS[i % COLORS.length],
            },
          })),
        },
      ],
    };
  }, [data.value, isDark]);

  if (data.loading) return <WidgetSkeleton className="h-72" />;
  if (data.error) return <WidgetError message={data.error} />;

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Sector Allocation
        </h3>
        {liveMeta && liveMeta.total_count > 0 && (
          <span
            data-testid="sector-allocation-live-chip"
            className={
              "inline-flex items-center gap-1 rounded-full "
              + "px-2 py-0.5 text-[10px] font-medium uppercase "
              + "tracking-wide "
              + (liveMeta.live_count > 0
                ? "bg-emerald-50 text-emerald-700 "
                  + "dark:bg-emerald-950/40 dark:text-emerald-300"
                : "bg-slate-100 text-slate-600 "
                  + "dark:bg-slate-800 dark:text-slate-400")
            }
            title={
              `Live LTP: ${liveMeta.live_count} · `
              + `EOD close: ${liveMeta.eod_count} · `
              + `Unknown: ${liveMeta.unknown_count}`
            }
          >
            {liveMeta.live_count > 0
              ? `● live · ${liveMeta.live_count}/`
                + `${liveMeta.total_count}`
              : `● eod close · ${liveMeta.eod_count}/`
                + `${liveMeta.total_count}`}
          </span>
        )}
      </div>
      <div className="px-3 py-2">
        <ReactECharts
          option={option}
          style={{ height: 240 }}
          opts={{ renderer: "canvas" }}
        />
      </div>
    </div>
  );
}
