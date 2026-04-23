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

interface Props {
  data: DashboardData<AllocationResponse>;
}

export function SectorAllocationWidget({ data }: Props) {
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
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
        <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Sector Allocation
        </h3>
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
