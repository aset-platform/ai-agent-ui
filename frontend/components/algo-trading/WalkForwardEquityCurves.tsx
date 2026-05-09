"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

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

export interface WindowCurve {
  windowIndex: number;
  testStart: string;
  testEnd: string;
  status: string;
  points: Array<{ bar_date: string; equity_inr: string }>;
}

interface Props {
  curves: WindowCurve[];
  initialCapitalInr: string;
  selectedIndices?: Set<number>;
  onSelectionChange?: (indices: Set<number>) => void;
}

/** Color palette for N stacked walk-forward window series.
 *  Blue → green gradient across windows.  Consistent in both
 *  light and dark modes (opacity handles the dark-mode shift). */
function windowColor(index: number, total: number): string {
  // Interpolate hue from 220 (blue) to 160 (teal/green)
  const t = total > 1 ? index / (total - 1) : 0;
  const hue = Math.round(220 - t * 60);
  return `hsl(${hue}, 70%, 55%)`;
}

function seriesName(c: WindowCurve): string {
  return `Window ${c.windowIndex + 1} (${c.testStart}…${c.testEnd})`;
}

export function WalkForwardEquityCurves({
  curves,
  initialCapitalInr,
  selectedIndices,
  onSelectionChange,
}: Props) {
  const isDark = useDarkMode();
  const total = curves.length;

  const option = useMemo(() => {
    const series = curves.map((c, i) => ({
      type: "line" as const,
      name: seriesName(c),
      showSymbol: false,
      lineStyle: {
        width: 2,
        color: windowColor(i, total),
        opacity: c.status === "completed" ? 1 : 0.35,
      },
      itemStyle: { color: windowColor(i, total) },
      data: c.points.map((p) => [p.bar_date, Number(p.equity_inr)]),
    }));

    const legendSelected: { [name: string]: boolean } = {};
    curves.forEach((c) => {
      legendSelected[seriesName(c)] =
        selectedIndices == null
          ? true
          : selectedIndices.has(c.windowIndex);
    });

    return {
      grid: { left: 60, right: 12, top: 32, bottom: 56 },
      xAxis: {
        type: "time" as const,
        axisLabel: { fontSize: 11 },
      },
      yAxis: {
        type: "value" as const,
        scale: true,
        axisLabel: { fontSize: 11 },
      },
      tooltip: {
        trigger: "axis" as const,
        formatter: (params: unknown[]) => {
          if (!Array.isArray(params) || !params.length) return "";
          const p0 = params[0] as { axisValueLabel: string };
          const lines = params.map((p: unknown) => {
            const item = p as {
              marker: string;
              seriesName: string;
              value: [string, number];
            };
            return `${item.marker}${item.seriesName}: ₹${item.value[1].toLocaleString("en-IN")}`;
          });
          return `${p0.axisValueLabel}<br/>${lines.join("<br/>")}`;
        },
      },
      legend: {
        type: "scroll" as const,
        bottom: 0,
        textStyle: { fontSize: 11 },
        selected: legendSelected,
      },
      dataZoom: [
        {
          type: "inside" as const,
          filterMode: "filter" as const,
        },
        {
          type: "slider" as const,
          bottom: 24,
          height: 18,
        },
      ],
      series,
    };
  }, [curves, total, selectedIndices]);

  const events = useMemo(
    () => ({
      legendselectchanged: (params: unknown) => {
        if (!onSelectionChange) return;
        const sel = (params as { selected: { [k: string]: boolean } })
          .selected;
        const next = new Set<number>();
        curves.forEach((c) => {
          if (sel[seriesName(c)]) next.add(c.windowIndex);
        });
        onSelectionChange(next);
      },
    }),
    [curves, onSelectionChange],
  );

  if (curves.length === 0) {
    return (
      <div
        className="flex h-64 items-center justify-center rounded-md border border-slate-200 dark:border-slate-700 text-sm text-slate-500"
        data-testid="walkforward-curves-empty"
      >
        No window data yet
      </div>
    );
  }

  const visibleCount =
    selectedIndices == null ? total : selectedIndices.size;

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-2"
      data-testid="walkforward-curves"
    >
      <div className="mb-1 px-1 text-xs text-slate-500">
        {visibleCount} of {total} window{total !== 1 ? "s" : ""}
        {visibleCount !== total ? " selected" : ""} · click legend
        to toggle · scroll/pinch to zoom
      </div>
      <ReactECharts
        option={option}
        notMerge={true}
        key={isDark ? "d" : "l"}
        style={{ height: 320, width: "100%" }}
        opts={{ renderer: "canvas" }}
        onEvents={events}
      />
    </div>
  );
}
