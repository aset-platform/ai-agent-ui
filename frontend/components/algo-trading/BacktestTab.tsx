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

export function BacktestTab() {
  const { rows: history } = useBacktestRuns();
  const [activeRunId, setActiveRunId] = useState<string | null>(
    null,
  );
  const effectiveRunId = activeRunId ?? history[0]?.run_id ?? null;
  const { run, error } = useBacktestRun(effectiveRunId);

  return (
    <div className="space-y-4" data-testid="backtest-tab">
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
        (run.status === "pending" || run.status === "running") && (
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
    </div>
  );
}
