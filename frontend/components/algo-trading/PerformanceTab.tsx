"use client";

import { useMemo } from "react";

import {
  usePerformanceRuns,
  type PerformanceRunRow,
} from "@/hooks/usePerformanceRuns";

interface StratAgg {
  strategy_id: string;
  strategy_name: string;
  total_runs: number;
  completed_runs: number;
  avg_pnl_pct: number | null;
  win_rate_pct: number | null;
  total_pnl_inr: number;
}

function aggregate(rows: PerformanceRunRow[]): StratAgg[] {
  const buckets = new Map<string, PerformanceRunRow[]>();
  for (const r of rows) {
    const arr = buckets.get(r.strategy_id) ?? [];
    arr.push(r);
    buckets.set(r.strategy_id, arr);
  }
  const out: StratAgg[] = [];
  for (const [sid, list] of buckets) {
    const completed = list.filter(
      (r) => r.status === "completed" && r.total_pnl_pct !== null,
    );
    const avg =
      completed.length > 0
        ? completed.reduce(
            (acc, r) => acc + Number(r.total_pnl_pct ?? 0),
            0,
          ) / completed.length
        : null;
    const winSum =
      completed.length > 0
        ? completed.reduce(
            (acc, r) => acc + Number(r.win_rate_pct ?? 0),
            0,
          ) / completed.length
        : null;
    const totalPnl = completed.reduce(
      (acc, r) => acc + Number(r.total_pnl_inr ?? 0),
      0,
    );
    out.push({
      strategy_id: sid,
      strategy_name: list[0].strategy_name,
      total_runs: list.length,
      completed_runs: completed.length,
      avg_pnl_pct: avg,
      win_rate_pct: winSum,
      total_pnl_inr: totalPnl,
    });
  }
  out.sort((a, b) => b.total_pnl_inr - a.total_pnl_inr);
  return out;
}

function fmtInr(v: number | null): string {
  if (v === null) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(v);
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v.toFixed(2)}%`;
}

export function PerformanceTab() {
  const { runs, loading, error } = usePerformanceRuns(50);
  const aggregates = useMemo(() => aggregate(runs), [runs]);

  return (
    <div className="space-y-4" data-testid="performance-tab">
      <div>
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Performance
        </h2>
        <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
          Strategy-vs-strategy diff across your most recent runs
          (backtest + paper). Per spec § 9.1 slice 9.
        </p>
      </div>

      {error && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="performance-error"
        >
          {error}
        </div>
      )}

      {!error && aggregates.length === 0 && !loading && (
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
          data-testid="performance-empty"
        >
          No completed runs yet. Run a backtest or kick off a
          paper run to populate this view.
        </div>
      )}

      {aggregates.length > 0 && (
        <div
          className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700"
          data-testid="performance-aggregates-table"
        >
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">
                  Strategy
                </th>
                <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">
                  Runs
                </th>
                <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">
                  Avg PnL %
                </th>
                <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">
                  Avg Win Rate
                </th>
                <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">
                  Total PnL ₹
                </th>
              </tr>
            </thead>
            <tbody>
              {aggregates.map((a) => {
                const positive = a.total_pnl_inr >= 0;
                return (
                  <tr
                    key={a.strategy_id}
                    className="border-t border-slate-200 dark:border-slate-700"
                    data-testid={`performance-strategy-${a.strategy_id}`}
                  >
                    <td className="px-3 py-1.5 font-medium text-slate-900 dark:text-slate-100">
                      {a.strategy_name}
                      <span className="ml-2 text-xs text-slate-500">
                        ({a.completed_runs}/{a.total_runs} done)
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">
                      {a.total_runs}
                    </td>
                    <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">
                      {fmtPct(a.avg_pnl_pct)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">
                      {fmtPct(a.win_rate_pct)}
                    </td>
                    <td
                      className={`px-3 py-1.5 text-right font-medium ${
                        positive
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400"
                      }`}
                    >
                      {fmtInr(a.total_pnl_inr)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {runs.length > 0 && (
        <div className="space-y-1.5">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Recent runs
          </h3>
          <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
            <table
              className="min-w-full text-sm"
              data-testid="performance-runs-table"
            >
              <thead className="bg-slate-50 dark:bg-slate-800">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">
                    Strategy
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">
                    Mode
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">
                    Status
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">
                    Started
                  </th>
                  <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">
                    PnL %
                  </th>
                  <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">
                    PnL ₹
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr
                    key={r.run_id}
                    className="border-t border-slate-200 dark:border-slate-700"
                  >
                    <td className="px-3 py-1.5 text-slate-900 dark:text-slate-100">
                      {r.strategy_name}
                    </td>
                    <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                      {r.mode}
                    </td>
                    <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                      {r.status}
                    </td>
                    <td className="px-3 py-1.5 font-mono text-xs text-slate-600 dark:text-slate-400">
                      {new Date(r.started_at).toLocaleString(
                        "en-IN",
                        { timeZone: "Asia/Kolkata" },
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">
                      {r.total_pnl_pct
                        ? `${Number(r.total_pnl_pct).toFixed(2)}%`
                        : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">
                      {r.total_pnl_inr
                        ? fmtInr(Number(r.total_pnl_inr))
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
