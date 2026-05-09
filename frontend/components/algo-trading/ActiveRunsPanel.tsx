"use client";

import { useEffect, useState } from "react";

import {
  startPaperRun,
  stopPaperRun,
  usePaperFixtures,
  usePaperRuns,
} from "@/hooks/usePaperRuns";
import { useStrategies } from "@/hooks/useStrategies";

export function ActiveRunsPanel() {
  const { runs, loading } = usePaperRuns();
  const { strategies } = useStrategies();
  const { fixtures } = usePaperFixtures();
  const [strategyId, setStrategyId] = useState<string>("");
  const [fixturePath, setFixturePath] = useState<string>("");
  const [capital, setCapital] = useState<string>("100000.00");
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Default to the first available fixture once loaded — usually
  // the larger ticks_indian_universe.jsonl, which is what users
  // actually want to replay against. Without this default users
  // see "Select fixture…" and have to click twice to start.
  useEffect(() => {
    if (!fixturePath && fixtures.length > 0) {
      // Prefer non-FAKE fixtures over the FAKE.NS smoke one.
      const real = fixtures.find(
        (f) => !f.sample_tickers.some((t) => t.startsWith("FAKE")),
      );
      setFixturePath((real ?? fixtures[0]).path);
    }
  }, [fixtures, fixturePath]);

  async function handleStart() {
    if (!strategyId) {
      setErr("Pick a strategy");
      return;
    }
    if (!fixturePath) {
      setErr("Pick a fixture");
      return;
    }
    setErr(null);
    setPending(strategyId);
    try {
      await startPaperRun(strategyId, fixturePath, capital);
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
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] text-slate-500">
            Strategy
          </span>
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
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] text-slate-500">
            Replay fixture
          </span>
          <select
            className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
            value={fixturePath}
            onChange={(e) => setFixturePath(e.target.value)}
            data-testid="paper-start-fixture-select"
          >
            {fixtures.length === 0 && (
              <option value="">Loading fixtures…</option>
            )}
            {fixtures.map((f) => (
              <option key={f.path} value={f.path}>
                {f.path} · {f.n_ticks} ticks · {f.distinct_tickers}{" "}
                ticker{f.distinct_tickers === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] text-slate-500">
            Capital ₹
          </span>
          <input
            type="number"
            min={1000}
            step={1000}
            value={capital}
            onChange={(e) => setCapital(e.target.value)}
            className="w-28 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
            data-testid="paper-start-capital"
          />
        </label>
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
