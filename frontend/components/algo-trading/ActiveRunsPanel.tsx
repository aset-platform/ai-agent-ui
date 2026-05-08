"use client";

import { useState } from "react";

import {
  startPaperRun,
  stopPaperRun,
  usePaperRuns,
} from "@/hooks/usePaperRuns";
import { useStrategies } from "@/hooks/useStrategies";

const REPLAY_FIXTURE = "ticks_sample.jsonl";

export function ActiveRunsPanel() {
  const { runs, loading } = usePaperRuns();
  const { strategies } = useStrategies();
  const [strategyId, setStrategyId] = useState<string>("");
  const [capital, setCapital] = useState<string>("100000.00");
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function handleStart() {
    if (!strategyId) {
      setErr("Pick a strategy");
      return;
    }
    setErr(null);
    setPending(strategyId);
    try {
      await startPaperRun(strategyId, REPLAY_FIXTURE, capital);
    } catch (exc) {
      setErr(exc instanceof Error ? exc.message : "Failed");
    } finally {
      setPending(null);
    }
  }

  async function handleStop(sid: string) {
    setPending(sid);
    setErr(null);
    try {
      await stopPaperRun(sid);
    } catch (exc) {
      setErr(exc instanceof Error ? exc.message : "Failed");
    } finally {
      setPending(null);
    }
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
      data-testid="paper-active-runs-panel"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Active runs
        </h3>
        <span className="text-xs text-slate-500">
          v1 = replay-fixture mode (live Kite WS in v2)
        </span>
      </div>

      <div
        className="mt-2 flex flex-wrap items-end gap-2"
        data-testid="paper-start-run-form"
      >
        <select
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          data-testid="paper-start-strategy-select"
        >
          <option value="">Select strategy…</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <input
          type="number"
          min={1000}
          step={1000}
          value={capital}
          onChange={(e) => setCapital(e.target.value)}
          className="w-28 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          data-testid="paper-start-capital"
        />
        <button
          type="button"
          onClick={handleStart}
          disabled={pending === strategyId}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          data-testid="paper-start-btn"
        >
          {pending === strategyId ? "Starting…" : "Start run"}
        </button>
      </div>

      {err && (
        <p
          className="mt-2 text-xs text-rose-600"
          data-testid="paper-start-error"
        >
          {err}
        </p>
      )}

      <div className="mt-3">
        {loading && runs.length === 0 ? (
          <p className="text-sm text-slate-500">Loading runs…</p>
        ) : runs.length === 0 ? (
          <p
            className="text-sm text-slate-500"
            data-testid="paper-active-runs-empty"
          >
            No active runs.
          </p>
        ) : (
          <ul className="space-y-1">
            {runs.map((r) => (
              <li
                key={`${r.user_id}-${r.strategy_id}`}
                className="flex items-center justify-between rounded border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-sm"
                data-testid={`paper-active-run-${r.strategy_id}`}
              >
                <div>
                  <span className="font-medium text-slate-900 dark:text-slate-100">
                    {r.strategy_name}
                  </span>
                  <span className="ml-2 text-xs text-slate-500">
                    started{" "}
                    {new Date(r.started_at).toLocaleTimeString(
                      "en-IN",
                      { timeZone: "Asia/Kolkata" },
                    )}{" "}
                    · {r.status}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleStop(r.strategy_id)}
                  disabled={pending === r.strategy_id}
                  className="rounded bg-rose-600 px-2.5 py-1 text-xs text-white disabled:opacity-60"
                  data-testid={`paper-stop-btn-${r.strategy_id}`}
                >
                  {pending === r.strategy_id ? "Stopping…" : "Stop"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
