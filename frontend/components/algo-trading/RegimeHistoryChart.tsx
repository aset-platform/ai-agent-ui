"use client";
/**
 * Regime history ribbon + HMM stress-line chart (REGIME-1).
 *
 * Renders the trailing 252 trading days as:
 * - A coloured ribbon (markArea) per contiguous regime run
 *   (BULL=emerald, SIDEWAYS=slate, BEAR=rose).
 * - A line series for `stress_prob` on a 0..1 axis.
 *
 * Uses ECharts via a dynamic import so the bundle stays
 * client-side only (next/dynamic ssr=false). The local
 * `useDarkMode` mirrors peer chart components in this folder
 * (WalkForwardEquityCurves, BacktestEquityCurve) — a
 * MutationObserver on `<html class="dark">`.
 */

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import "@/lib/echarts";
import {
  useRegimeHistory,
  type RegimeHistoryRow,
} from "@/hooks/useRegime";

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

const REGIME_COLORS: Record<string, string> = {
  BULL: "rgba(16, 185, 129, 0.12)",      // emerald
  SIDEWAYS: "rgba(148, 163, 184, 0.10)", // slate
  BEAR: "rgba(244, 63, 94, 0.14)",       // rose
};

interface Band {
  start: string;
  end: string;
  label: string;
}

function compressToBands(rows: RegimeHistoryRow[]): Band[] {
  if (rows.length === 0) return [];
  const out: Band[] = [];
  let runStart = rows[0].bar_date;
  let runLabel = rows[0].regime_label;
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].regime_label !== runLabel) {
      out.push({
        start: runStart,
        end: rows[i - 1].bar_date,
        label: runLabel,
      });
      runStart = rows[i].bar_date;
      runLabel = rows[i].regime_label;
    }
  }
  out.push({
    start: runStart,
    end: rows[rows.length - 1].bar_date,
    label: runLabel,
  });
  return out;
}

export function RegimeHistoryChart() {
  const isDark = useDarkMode();
  const { rows, loading, error } = useRegimeHistory(252);

  const option = useMemo(() => {
    const bands = compressToBands(rows);
    return {
      backgroundColor: "transparent",
      grid: { left: 40, right: 20, top: 16, bottom: 32 },
      xAxis: {
        type: "category",
        data: rows.map((r) => r.bar_date),
        axisLabel: { fontSize: 10 },
      },
      yAxis: {
        type: "value",
        name: "stress_prob",
        min: 0,
        max: 1,
        axisLabel: { fontSize: 10 },
      },
      tooltip: { trigger: "axis" },
      series: [
        {
          name: "Stress",
          type: "line",
          data: rows.map((r) => r.stress_prob ?? null),
          showSymbol: false,
          lineStyle: { color: isDark ? "#94a3b8" : "#475569" },
          markArea: {
            silent: true,
            itemStyle: { opacity: 1 },
            data: bands.map((b) => [
              {
                xAxis: b.start,
                itemStyle: { color: REGIME_COLORS[b.label] },
                name: b.label,
              },
              { xAxis: b.end },
            ]),
          },
        },
      ],
    };
  }, [rows, isDark]);

  if (loading) {
    return (
      <p className="text-xs text-slate-500">
        Loading regime history…
      </p>
    );
  }
  if (error || rows.length === 0) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="regime-history-empty"
      >
        No regime history yet.
      </p>
    );
  }

  return (
    <div
      className="rounded-md border border-slate-200
        dark:border-slate-700 p-2"
      data-testid="regime-history-chart"
    >
      <ReactECharts
        option={option}
        notMerge
        style={{ height: 220, width: "100%" }}
        key={isDark ? "d" : "l"}
      />
    </div>
  );
}
