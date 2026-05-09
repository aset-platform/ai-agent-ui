"use client";

import { useEffect, useState } from "react";

import { useBrokerStatus } from "@/hooks/useBrokerStatus";
import {
  type PaperRunSource,
  type RunMode,
  startPaperRun,
  stopPaperRun,
  usePaperFixtures,
  usePaperRuns,
} from "@/hooks/usePaperRuns";
import { useStrategies } from "@/hooks/useStrategies";

interface Props {
  /** Trading mode this panel is rendered under. Determines:
   *  - Which sources are offered (Paper: replay only;
   *    Dry run: replay or live-ws; Live: live-ws only)
   *  - The mode= field sent to POST /v1/algo/paper/runs which
   *    routes the runtime (paper → PaperRuntime; live →
   *    LiveRuntime)
   *  - The Start button label */
  tradingMode?: "paper" | "dryrun" | "live";
}

export function ActiveRunsPanel({ tradingMode = "paper" }: Props) {
  const { runs: allRuns, loading } = usePaperRuns();
  // Filter the active-runs list to only those that match the
  // current trading view. Without this filter, a Live run
  // started from the Live tab leaks into the Paper view's
  // active list — confusing because that run won't emit
  // mode='paper' events.
  const runs = allRuns.filter((r) => {
    if (tradingMode === "paper") return r.mode === "paper";
    if (tradingMode === "live") {
      return r.mode === "live" && !r.dry_run;
    }
    // dryrun
    return r.mode === "live" && r.dry_run;
  });
  const { strategies } = useStrategies();
  const { fixtures } = usePaperFixtures();
  const { value: brokerStatus } = useBrokerStatus();
  const [strategyId, setStrategyId] = useState<string>("");
  const [fixturePath, setFixturePath] = useState<string>("");
  const [capital, setCapital] = useState<string>("100000.00");
  // Default source per trading mode. Live mode requires real
  // ticks; paper mode is replay-only by design.
  const initialSource: PaperRunSource =
    tradingMode === "live" ? "live-ws" : "replay";
  const [source, setSource] = useState<PaperRunSource>(initialSource);
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const kiteConnected = brokerStatus?.status === "connected";

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
    if (source === "replay" && !fixturePath) {
      setErr("Pick a fixture");
      return;
    }
    setErr(null);
    setPending(strategyId);
    try {
      // Map UI tradingMode -> backend RunMode. Both 'dryrun'
      // and 'live' route to LiveRuntime; ALGO_LIVE_DRY_RUN env
      // controls whether KiteAdapter short-circuits.
      const runMode: RunMode =
        tradingMode === "paper" ? "paper" : "live";
      await startPaperRun(
        strategyId, fixturePath, capital, source, runMode,
      );
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
      </div>

      <div
        className="mt-3 flex flex-col gap-3"
        data-testid="paper-start-run-form"
      >
        {/* Row 1 — Source.  Paper mode is replay-only by design;
            Live mode is live-ws-only (real ticks for real money);
            Dry run accepts both — replay for offline rehearsal,
            live-ws for real-tick rehearsal during market hours. */}
        {tradingMode !== "paper" && tradingMode !== "live" && (
          <fieldset
            className="flex flex-col gap-1"
            data-testid="paper-source-radio-group"
          >
            <legend className="text-[11px] uppercase tracking-wide font-medium text-slate-500">
              Source
            </legend>
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="paper-source"
                  value="replay"
                  checked={source === "replay"}
                  onChange={() => setSource("replay")}
                  data-testid="paper-source-replay"
                />
                Replay fixture
              </label>
              <label
                className={`flex items-center gap-1.5 text-sm cursor-pointer ${
                  !kiteConnected
                    ? "opacity-50 cursor-not-allowed"
                    : ""
                }`}
                title={
                  !kiteConnected
                    ? "Connect Zerodha to enable live WS"
                    : undefined
                }
              >
                <input
                  type="radio"
                  name="paper-source"
                  value="live-ws"
                  checked={source === "live-ws"}
                  onChange={() => {
                    if (kiteConnected) setSource("live-ws");
                  }}
                  disabled={!kiteConnected}
                  data-testid="paper-source-live-ws"
                />
                Live Kite WS
              </label>
              {source === "live-ws" && (
                <span
                  className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"
                  data-testid="paper-live-ws-indicator"
                >
                  <span
                    className="inline-block w-2 h-2 rounded-full bg-emerald-500"
                    aria-hidden="true"
                  />
                  Streaming from Kite WS
                </span>
              )}
            </div>
          </fieldset>
        )}
        {tradingMode === "live" && source === "live-ws" && (
          <span
            className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"
            data-testid="paper-live-ws-indicator"
          >
            <span
              className="inline-block w-2 h-2 rounded-full bg-emerald-500"
              aria-hidden="true"
            />
            Streaming from Kite WS — real money, real fills
          </span>
        )}

        {/* Row 2 — Strategy + (when replay) Fixture */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wide font-medium text-slate-500">
              Strategy
            </span>
            <select
              className="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm"
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

          {source === "replay" && tradingMode !== "live" ? (
            <label className="flex flex-col gap-1">
              <span className="text-[11px] uppercase tracking-wide font-medium text-slate-500">
                Replay fixture
              </span>
              <select
                className="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm"
                value={fixturePath}
                onChange={(e) => setFixturePath(e.target.value)}
                data-testid="paper-start-fixture-select"
              >
                {fixtures.length === 0 && (
                  <option value="">Loading fixtures…</option>
                )}
                {fixtures.map((f) => (
                  <option key={f.path} value={f.path}>
                    {f.path} · {f.n_ticks} ticks ·{" "}
                    {f.distinct_tickers}{" "}
                    ticker{f.distinct_tickers === 1 ? "" : "s"}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <div aria-hidden="true" />
          )}
        </div>

        {/* Row 3 — Capital + Start */}
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wide font-medium text-slate-500">
              Capital ₹
            </span>
            <input
              type="number"
              min={1000}
              step={1000}
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
              className="w-36 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm"
              data-testid="paper-start-capital"
            />
          </label>
          <button
            type="button"
            onClick={handleStart}
            disabled={pending === strategyId}
            className="ml-auto rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60 disabled:cursor-not-allowed"
            data-testid="paper-start-btn"
          >
            {pending === strategyId ? "Starting…" : "Start run"}
          </button>
        </div>
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
