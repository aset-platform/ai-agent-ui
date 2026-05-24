"use client";

import { useState } from "react";

import {
  useBacktestRun,
  useBacktestRuns,
} from "@/hooks/useBacktestRuns";

import { BacktestEquityCurve } from "./BacktestEquityCurve";
import { BacktestRunForm } from "./BacktestRunForm";
import { BacktestSummaryCards } from "./BacktestSummaryCards";
import { BacktestTradeTable } from "./BacktestTradeTable";
import { SweepSubTab } from "./SweepSubTab";
import { WalkForwardSubTab } from "./WalkForwardSubTab";

type SubTab = "single" | "walkforward" | "parameter_sweep";

export function BacktestTab() {
  const [subTab, setSubTab] = useState<SubTab>("single");
  const { rows: history } = useBacktestRuns();
  const [activeRunId, setActiveRunId] = useState<string | null>(
    null,
  );
  const effectiveRunId = activeRunId ?? history[0]?.run_id ?? null;
  const { run, error } = useBacktestRun(effectiveRunId);

  return (
    <div className="space-y-4" data-testid="backtest-tab">
      {/* Sub-tab strip */}
      <div
        className="flex gap-1 border-b border-slate-200 dark:border-slate-700"
        data-testid="backtest-sub-tab-strip"
      >
        {(
          [
            { id: "single", label: "Single run" },
            { id: "walkforward", label: "Walk-forward CV" },
            { id: "parameter_sweep", label: "Parameter sweep" },
          ] as const
        ).map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setSubTab(id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              subTab === id
                ? "border-indigo-600 text-indigo-600 dark:text-indigo-400"
                : "border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
            }`}
            data-testid={`backtest-sub-tab-${id}`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── Single run ─────────────────────────────────────── */}
      {subTab === "single" && (
        <>
          <BacktestRunForm onSubmitted={(id) => setActiveRunId(id)} />

          {history.length > 0 && (
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="text-slate-500">Recent:</span>
              {history.slice(0, 8).map((h) => (
                <button
                  key={h.run_id}
                  onClick={() => setActiveRunId(h.run_id)}
                  className={`rounded px-2 py-0.5 border ${
                    h.run_id === effectiveRunId
                      ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30"
                      : "border-slate-200 dark:border-slate-700"
                  }`}
                  data-testid={`backtest-history-${h.run_id}`}
                >
                  {h.period_start}…{h.period_end} · {h.status}
                </button>
              ))}
            </div>
          )}

          {error && (
            <div
              className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
              data-testid="backtest-load-error"
            >
              {error}
            </div>
          )}

          {run && run.status === "failed" && (
            <div
              className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
              data-testid="backtest-run-error"
            >
              Run failed: {run.error_text ?? "unknown error"}
            </div>
          )}

          {run &&
            (run.status === "pending" ||
              run.status === "running") && (
              <div
                className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
                data-testid="backtest-run-progress"
              >
                Run is {run.status}…
              </div>
            )}

          {run && run.status === "completed" && (
            <>
              <BacktestSummaryCards summary={run} />
              <BacktestEquityCurve
                points={run.equity_curve}
                initialCapitalInr={run.initial_capital_inr}
              />
              <BacktestTradeTable rows={run.trade_list} />
            </>
          )}
        </>
      )}

      {/* ── Walk-forward CV ────────────────────────────────── */}
      {subTab === "walkforward" && <WalkForwardSubTab />}

      {/* ── Parameter sweep ────────────────────────────────── */}
      {subTab === "parameter_sweep" && <SweepSubTab />}
    </div>
  );
}
