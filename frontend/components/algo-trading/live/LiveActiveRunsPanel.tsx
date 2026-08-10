"use client";

/**
 * LiveActiveRunsPanel — Start / Stop control + currently-running
 * list, scoped to mode="live" runs ONLY.
 *
 * ASETPLTFRM-378 — closes the UX gap left by the three-page split.
 * The Live page now has its own Start Runtime UI instead of relying
 * on the Strategies → Dry-run tab (which also previously controlled
 * the live spawn through the Redis dry-run flag — a misfeature).
 *
 * Wires to startPaperRun(..., "live") with a literal "live" mode.
 * The backend (slice C-backend) pins KiteClient.dry_run=False for
 * this path explicitly; the per-user Redis flag is no longer
 * consulted.
 */

import { useState } from "react";

import { formatIstTime } from "@/lib/datetime";
import { useBrokerStatus } from "@/hooks/useBrokerStatus";
import {
  startPaperRun,
  stopPaperRun,
  usePaperRuns,
} from "@/hooks/usePaperRuns";
import {
  filterStrategiesByMode,
  useStrategies,
} from "@/hooks/useStrategies";

interface StartConfirmModalProps {
  strategyName: string;
  onConfirm: () => void;
  onCancel: () => void;
  pending: boolean;
  error: string | null;
}

/** Typed-confirmation gate on every live-runtime start, mirroring
 * LiveModeToggle's EnableConfirmModal. Caps.live_orders_enabled is
 * a one-time toggle — once flipped (e.g. during earlier setup), a
 * bare "Start live runtime" click on any later day places real
 * Kite orders with no further confirmation. Found 2026-08-10: a
 * dry-run rehearsal was intended but this panel (Live tab, no
 * dry-run option) was clicked instead, and the run went live
 * immediately since caps were already enabled from prior testing. */
function StartLiveConfirmModal({
  strategyName,
  onConfirm,
  onCancel,
  pending,
  error,
}: StartConfirmModalProps) {
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === strategyName;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center
        bg-black/50 backdrop-blur-sm"
      data-testid="live-start-confirm-modal-overlay"
    >
      <div
        className="w-full max-w-sm rounded-lg border border-slate-200
          bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900"
        data-testid="live-start-confirm-modal"
      >
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Start live runtime
        </h3>
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
          Real orders will be placed via Kite. Losses are possible.
          <br />
          Type the strategy name to confirm:
          <strong className="block mt-1 text-slate-800 dark:text-slate-200">
            {strategyName}
          </strong>
        </p>

        <input
          type="text"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Retype strategy name"
          autoFocus
          className="mt-3 w-full rounded border border-slate-300 px-2 py-1.5
            text-sm dark:border-slate-600 dark:bg-slate-800
            dark:text-slate-100"
          data-testid="live-start-confirm-modal-name-input"
        />

        {error && (
          <p
            className="mt-2 text-xs text-rose-600"
            data-testid="live-start-confirm-modal-error"
          >
            {error}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="rounded border border-slate-300 px-3 py-1 text-sm
              dark:border-slate-600"
            data-testid="live-start-confirm-modal-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!matches || pending}
            className="rounded bg-rose-600 px-3 py-1 text-sm text-white
              hover:bg-rose-700 disabled:opacity-50"
            data-testid="live-start-confirm-modal-confirm"
          >
            {pending ? "Starting…" : "Start live runtime"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface Props {
  /** Selected strategy id, hoisted to LiveDashboard so all Live
   *  page dropdowns (Start picker, safety belts, attribution)
   *  stay in sync. Optional for backward compat — when absent
   *  the panel falls back to a local useState. */
  strategyId?: string;
  onStrategyChange?: (id: string) => void;
}

export function LiveActiveRunsPanel({
  strategyId: controlledStrategyId,
  onStrategyChange,
}: Props = {}) {
  const { runs: allRuns, loading } = usePaperRuns();
  // Live-only filter — dry-run runs land in the Strategies →
  // Dry-run tab's panel, never here.
  const runs = allRuns.filter(
    (r) => r.mode === "live" && !r.dry_run,
  );
  // Live-only filter mirrors LiveDashboard. Promotion workflow
  // keeps non-live strategies out of the real-money picker.
  const { strategies: allStrategies } = useStrategies();
  const strategies = filterStrategiesByMode(allStrategies, ["live"]);
  const { value: brokerStatus } = useBrokerStatus();
  // Controlled-or-uncontrolled pattern: if the parent passes a
  // strategyId we mirror it; otherwise we maintain our own.
  const [localStrategyId, setLocalStrategyId] = useState<string>("");
  const strategyId = (
    controlledStrategyId !== undefined
      ? controlledStrategyId
      : localStrategyId
  );
  const setStrategyId = (id: string) => {
    if (onStrategyChange) {
      onStrategyChange(id);
    } else {
      setLocalStrategyId(id);
    }
  };
  const [capital, setCapital] = useState<string>("100000.00");
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showStartConfirm, setShowStartConfirm] = useState(false);

  const kiteConnected = brokerStatus?.status === "connected";
  const selectedStrategyName =
    strategies.find((s) => s.id === strategyId)?.name ?? "";

  function handleStart() {
    if (!strategyId) {
      setErr("Pick a strategy");
      return;
    }
    if (!kiteConnected) {
      setErr("Connect Zerodha first");
      return;
    }
    setErr(null);
    setShowStartConfirm(true);
  }

  async function doStart() {
    setErr(null);
    setPending(strategyId);
    try {
      await startPaperRun(
        strategyId,
        "",
        capital,
        "live-ws",
        "live",
      );
      setShowStartConfirm(false);
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
    <>
    <div
      className="rounded-md border border-rose-200 dark:border-rose-900/40 bg-rose-50/30 dark:bg-rose-950/10 p-3"
      data-testid="live-active-runs-panel"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Live runtime
        </h3>
        <span
          className="text-[10px] uppercase tracking-wide font-medium text-rose-700 dark:text-rose-400"
          data-testid="live-active-runs-mode-pill"
        >
          Real money · real fills
        </span>
      </div>

      <div
        className="mt-3 flex flex-col gap-3"
        data-testid="live-start-run-form"
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wide font-medium text-slate-500">
              Strategy
            </span>
            <select
              className="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              data-testid="live-start-strategy-select"
            >
              <option value="">Select strategy…</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>

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
              className="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm"
              data-testid="live-start-capital"
            />
          </label>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <span
            className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"
            data-testid="live-ws-indicator"
          >
            <span
              className={`inline-block w-2 h-2 rounded-full ${
                kiteConnected ? "bg-emerald-500" : "bg-slate-400"
              }`}
              aria-hidden="true"
            />
            {kiteConnected
              ? "Streaming from Kite WS — real money, real fills"
              : "Connect Zerodha to enable live trading"}
          </span>

          <button
            type="button"
            onClick={handleStart}
            disabled={
              pending === strategyId || !kiteConnected
            }
            className="rounded bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-60 disabled:cursor-not-allowed"
            data-testid="live-start-btn"
          >
            {pending === strategyId
              ? "Starting…"
              : "Start live runtime"}
          </button>
        </div>
      </div>

      {err && (
        <p
          className="mt-2 text-xs text-rose-600"
          data-testid="live-start-error"
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
            data-testid="live-active-runs-empty"
          >
            No live runtime active.
          </p>
        ) : (
          <ul className="space-y-1">
            {runs.map((r) => (
              <li
                key={`${r.user_id}-${r.strategy_id}`}
                className="flex items-center justify-between rounded border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-sm"
                data-testid={`live-active-run-${r.strategy_id}`}
              >
                <div>
                  <span className="font-medium text-slate-900 dark:text-slate-100">
                    {r.strategy_name}
                  </span>
                  <span className="ml-2 text-xs text-slate-500">
                    started {formatIstTime(r.started_at)} · {r.status}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleStop(r.strategy_id)}
                  disabled={pending === r.strategy_id}
                  className="rounded bg-rose-600 px-2.5 py-1 text-xs text-white disabled:opacity-60"
                  data-testid={`live-stop-btn-${r.strategy_id}`}
                >
                  {pending === r.strategy_id ? "Stopping…" : "Stop"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>

    {showStartConfirm && (
      <StartLiveConfirmModal
        strategyName={selectedStrategyName}
        onConfirm={doStart}
        onCancel={() => {
          setShowStartConfirm(false);
          setErr(null);
        }}
        pending={pending === strategyId}
        error={err}
      />
    )}
    </>
  );
}
