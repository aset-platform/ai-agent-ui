"use client";
/**
 * Regime history ribbon + HMM stress-line chart (REGIME-1).
 *
 * Renders the trailing 252 trading days as:
 * - A coloured ribbon (markArea) per contiguous regime run
 *   (BULL=emerald, SIDEWAYS=slate, BEAR=rose).
 * - A line series for HMM stress probability on a 0..1 axis,
 *   with band labels (Calm / Transitional / Stressed) shown
 *   in the tooltip alongside the rule-based regime label.
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

// HMM stress-probability bands. Cutoffs match the regime-widget
// divergence chip thresholds — keep in sync if those move.
function stressBand(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "—";
  if (p < 0.3) return "Calm";
  if (p < 0.6) return "Transitional";
  if (p < 0.8) return "Stressed";
  return "High stress";
}

function stressBandColor(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) {
    return "#94a3b8"; // slate-400
  }
  if (p < 0.3) return "#10b981";   // emerald-500
  if (p < 0.6) return "#f59e0b";   // amber-500
  return "#f43f5e";                 // rose-500
}

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
    // Map bar_date → regime_label for the tooltip lookup.
    const regimeByDate: Record<string, string> = {};
    for (const r of rows) {
      regimeByDate[r.bar_date] = r.regime_label;
    }
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
        // User-readable axis label. Title was "stress_prob" — too
        // engineery for non-quants. Tooltip carries the full
        // explanation.
        name: "Market stress",
        nameTextStyle: { fontSize: 11 },
        min: 0,
        max: 1,
        axisLabel: { fontSize: 10 },
      },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          // ECharts passes an array of point payloads. We trigger
          // on axis so params[0] carries the date + value for our
          // single line series.
          const p = Array.isArray(params) ? params[0] : params;
          const item = p as {
            axisValue?: string;
            value?: number | null;
            data?: number | null;
          };
          const date = item.axisValue ?? "";
          const v = (item.value ?? item.data) as number | null;
          const regime = regimeByDate[date] ?? "—";
          const band = stressBand(v);
          const colour = stressBandColor(v);
          const vStr = v == null || Number.isNaN(v)
            ? "—"
            : v.toFixed(2);
          return [
            `<div style="font-weight:600;margin-bottom:4px;">${date}</div>`,
            `<div>Regime: <strong>${regime}</strong></div>`,
            `<div>Stress: <strong>${vStr}</strong>`,
            ` <span style="color:${colour};font-weight:600;">`,
            `(${band})</span></div>`,
          ].join("");
        },
      },
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
          // Reference lines for the band thresholds. Keeps the
          // user oriented even before they hover.
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: {
              type: "dashed",
              opacity: 0.35,
              width: 1,
            },
            data: [
              {
                yAxis: 0.3,
                lineStyle: { color: "#f59e0b" },
                label: {
                  formatter: "Transitional",
                  fontSize: 10,
                  color: "#f59e0b",
                  position: "insideEndTop",
                },
              },
              {
                yAxis: 0.6,
                lineStyle: { color: "#f43f5e" },
                label: {
                  formatter: "Stressed",
                  fontSize: 10,
                  color: "#f43f5e",
                  position: "insideEndTop",
                },
              },
            ],
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
      <div className="mb-1 flex items-center justify-between gap-3 px-1">
        <div className="text-[11px] font-medium text-slate-600 dark:text-slate-300">
          Market stress (HMM advisory)
        </div>
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
            Calm &lt; 0.3
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
            Transitional 0.3–0.6
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-rose-500" />
            Stressed &gt; 0.6
          </span>
        </div>
      </div>
      <ReactECharts
        option={option}
        notMerge
        style={{ height: 220, width: "100%" }}
        key={isDark ? "d" : "l"}
      />
    </div>
  );
}
