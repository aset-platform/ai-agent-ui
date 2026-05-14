"use client";
/**
 * Promote-workflow modal for the Strategies tab.
 *
 * Opens with a strategy id; fetches eligibility from
 * GET /v1/algo/strategies/{id}/mode-transitions/eligibility and
 * renders one card per target mode (paper, live).
 *
 * Each card surfaces:
 * - Why the gate fails (list of reasons from the backend).
 * - A primary Promote button when the gate is satisfied.
 * - A separate Bypass card (live target only) when the strategy
 *   has earned the right by having been live before; the bypass
 *   requires typing the strategy name AND a freeform reason that
 *   lands on the audit row.
 *
 * No silent shortcuts — every confirmation has friction sized to
 * the risk it accepts.
 */

import { useEffect, useState } from "react";

import {
  fetchEligibility,
  setStrategyMode,
  type EligibilityResponse,
  type StrategyMode,
  type StrategySummary,
  type TransitionEligibility,
} from "@/hooks/useStrategies";

interface Props {
  strategy: StrategySummary;
  open: boolean;
  onClose: () => void;
  onPromoted: () => void;
}

export function PromoteModal({
  strategy,
  open,
  onClose,
  onPromoted,
}: Props) {
  const [elig, setElig] = useState<EligibilityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setElig(null);
    setLoading(true);
    fetchEligibility(strategy.id)
      .then(setElig)
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [open, strategy.id]);

  // Escape closes
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="promote-modal"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl mx-4 rounded-2xl shadow-xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 p-5 space-y-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Promote strategy
          </h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {strategy.name} · current mode:{" "}
            <code className="font-medium">
              {elig?.current_mode ?? strategy.mode}
            </code>
          </p>
        </div>

        {loading && (
          <p className="text-sm text-gray-500">
            Checking workflow gates…
          </p>
        )}
        {error && (
          <p
            role="alert"
            className="rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-xs p-2"
          >
            {error}
          </p>
        )}

        {elig?.transitions.map((t) => (
          <TransitionCard
            key={t.target}
            strategy={strategy}
            transition={t}
            currentMode={elig.current_mode}
            onDone={() => {
              onPromoted();
              onClose();
            }}
          />
        ))}

        <div className="flex justify-end pt-2">
          <button
            type="button"
            onClick={onClose}
            data-testid="promote-modal-close"
            className="rounded-lg px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function TransitionCard({
  strategy,
  transition,
  currentMode,
  onDone,
}: {
  strategy: StrategySummary;
  transition: TransitionEligibility;
  currentMode: StrategyMode;
  onDone: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const targetLabel =
    transition.target.charAt(0).toUpperCase() +
    transition.target.slice(1);

  const handlePromote = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await setStrategyMode(strategy.id, {
        mode: transition.target,
        bypass: false,
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-md border border-gray-200 dark:border-gray-700 bg-slate-50/50 dark:bg-gray-900/40 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
          → {targetLabel}
        </div>
        {transition.allowed ? (
          <button
            type="button"
            onClick={handlePromote}
            disabled={submitting}
            data-testid={`promote-confirm-${transition.target}`}
            className="rounded-md bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-3 py-1 text-xs"
          >
            {submitting ? "Promoting…" : `Promote to ${targetLabel}`}
          </button>
        ) : (
          <span className="text-xs text-gray-500">
            Gate not satisfied
          </span>
        )}
      </div>

      {!transition.allowed && transition.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-gray-600 dark:text-gray-400 space-y-1">
          {transition.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}

      {error && (
        <p
          role="alert"
          className="text-xs text-red-600 dark:text-red-400"
        >
          {error}
        </p>
      )}

      {transition.target === "live" && transition.bypass_available && (
        <BypassCard
          strategy={strategy}
          currentMode={currentMode}
          onDone={onDone}
        />
      )}
    </div>
  );
}

function BypassCard({
  strategy,
  currentMode,
  onDone,
}: {
  strategy: StrategySummary;
  currentMode: StrategyMode;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [typedName, setTypedName] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nameMatches = typedName.trim() === strategy.name;
  const reasonProvided = reason.trim().length > 0;
  const canConfirm = nameMatches && reasonProvided && !submitting;

  const handleBypass = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await setStrategyMode(strategy.id, {
        mode: "live",
        bypass: true,
        reason: reason.trim(),
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="promote-open-bypass"
        className="text-xs text-amber-700 dark:text-amber-300 underline hover:no-underline"
      >
        This strategy was previously live — promote directly to live
        (bypass gates)
      </button>
    );
  }

  return (
    <div className="rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 p-3 space-y-2">
      <p className="text-xs font-medium text-amber-800 dark:text-amber-300">
        Bypass workflow gates — promote {currentMode} → live
      </p>
      <p className="text-xs text-amber-700 dark:text-amber-400">
        Skips the {currentMode === "draft"
          ? "backtest + walk-forward + paper-run"
          : "paper-run"}{" "}
        validation. The audit row stamps your reason; future
        forensic reviews can see exactly what was overridden.
      </p>
      {strategy.open_position_count != null &&
        strategy.open_position_count > 0 && (
          <p className="text-xs text-rose-700 dark:text-rose-400">
            ⚠ {strategy.open_position_count} open position(s) still
            attached to prior {strategy.active_runtime_modes?.join(
              " + ",
            ) || "live"}{" "}
            runtime(s). A new live runtime may interact with them.
          </p>
        )}
      <label className="block text-xs text-amber-800 dark:text-amber-200">
        Type the strategy name to confirm:
        <input
          type="text"
          value={typedName}
          onChange={(e) => setTypedName(e.target.value)}
          placeholder={strategy.name}
          data-testid="promote-bypass-name"
          className="mt-1 w-full rounded border border-amber-400 dark:border-amber-600 bg-white dark:bg-gray-900 px-2 py-1 text-xs"
        />
      </label>
      <label className="block text-xs text-amber-800 dark:text-amber-200">
        Reason (recorded on the audit row):
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Hotfix re-promotion after spelling fix in alert text…"
          data-testid="promote-bypass-reason"
          rows={2}
          className="mt-1 w-full rounded border border-amber-400 dark:border-amber-600 bg-white dark:bg-gray-900 px-2 py-1 text-xs"
        />
      </label>
      {error && (
        <p
          role="alert"
          className="text-xs text-red-600 dark:text-red-400"
        >
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded px-2 py-1 text-xs text-amber-800 dark:text-amber-200 hover:bg-amber-100 dark:hover:bg-amber-900/40"
        >
          Cancel bypass
        </button>
        <button
          type="button"
          onClick={handleBypass}
          disabled={!canConfirm}
          data-testid="promote-bypass-confirm"
          className="rounded-md bg-rose-600 hover:bg-rose-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-3 py-1 text-xs"
        >
          {submitting ? "Promoting…" : "Bypass + Promote to live"}
        </button>
      </div>
    </div>
  );
}
