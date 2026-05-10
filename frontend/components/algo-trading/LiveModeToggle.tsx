"use client";
/**
 * LiveModeToggle — V2-5.
 *
 * 4-gate live trading enable/disable toggle.
 *
 * Gates (ALL must pass to enable):
 *   1. Kite connected — access_token in DB
 *   2. Caps set — max_inr > 0 AND allowed_tickers non-empty
 *   3. Kill switch DISARMED
 *   4. Walk-forward run < 30 days old AND positive win-rate
 *      + bonus: no symbol with drift > 3 consecutive runs
 *
 * Enable flow (2-step confirm):
 *   1. User clicks "Enable live orders"
 *   2. Modal appears — user must retype the strategy name exactly
 *   3. Confirm sends POST /algo/live/enable/{strategy_id}
 *      which validates gates server-side again (never trust UI)
 *
 * Disable is always allowed with a single click + confirm.
 *
 * z-index: modal at z-[70] (per §5.6 modal stacking rules)
 */

import { useState } from "react";

import {
  disableLiveOrders,
  enableLiveOrders,
} from "@/hooks/useLiveCaps";
import type { GatesStatus } from "@/hooks/useLiveStatus";
import { useLiveStatus } from "@/hooks/useLiveStatus";

interface Props {
  strategyId: string;
  strategyName: string;
  onToggled?: () => void;
}

const GATE_LABELS: Record<keyof Omit<GatesStatus, "all_pass" | "live_orders_enabled">, string> = {
  kite_connected: "Kite connected",
  caps_set: "Caps configured (max ₹ + tickers)",
  kill_switch_disarmed: "Kill switch disarmed",
  walkforward_recent: "Walk-forward < 30 days + positive win-rate",
  drift_within_limit: "No symbol with consecutive drift > 3 runs",
};

function GateBadge({
  label,
  passed,
}: {
  label: string;
  passed: boolean;
}) {
  return (
    <li
      className="flex items-center gap-2 text-xs"
      data-testid={`live-gate-${label.replace(/\s+/g, "-").toLowerCase()}`}
    >
      <span
        className={`flex-shrink-0 w-4 h-4 rounded-full flex items-center justify-center
          text-white text-[10px] font-bold
          ${passed
            ? "bg-emerald-500"
            : "bg-slate-300 dark:bg-slate-600"
          }`}
        aria-hidden="true"
      >
        {passed ? "✓" : "✕"}
      </span>
      <span
        className={
          passed
            ? "text-slate-700 dark:text-slate-300"
            : "text-slate-400 dark:text-slate-500"
        }
      >
        {label}
      </span>
    </li>
  );
}

interface EnableModalProps {
  strategyName: string;
  onConfirm: (typed: string) => void;
  onCancel: () => void;
  pending: boolean;
  error: string | null;
}

function EnableConfirmModal({
  strategyName,
  onConfirm,
  onCancel,
  pending,
  error,
}: EnableModalProps) {
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === strategyName;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center
        bg-black/50 backdrop-blur-sm"
      data-testid="live-enable-modal-overlay"
    >
      <div
        className="w-full max-w-sm rounded-lg border border-slate-200
          bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900"
        data-testid="live-enable-modal"
      >
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Enable live trading
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
          data-testid="live-enable-modal-name-input"
        />

        {error && (
          <p
            className="mt-2 text-xs text-rose-600"
            data-testid="live-enable-modal-error"
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
            data-testid="live-enable-modal-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(typed.trim())}
            disabled={!matches || pending}
            className="rounded bg-indigo-600 px-3 py-1 text-sm text-white
              hover:bg-indigo-700 disabled:opacity-50"
            data-testid="live-enable-modal-confirm"
          >
            {pending ? "Enabling…" : "Enable live orders"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface DisableModalProps {
  onConfirm: () => void;
  onCancel: () => void;
  pending: boolean;
}

function DisableConfirmModal({
  onConfirm,
  onCancel,
  pending,
}: DisableModalProps) {
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center
        bg-black/50 backdrop-blur-sm"
      data-testid="live-disable-modal-overlay"
    >
      <div
        className="w-full max-w-sm rounded-lg border border-slate-200
          bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900"
        data-testid="live-disable-modal"
      >
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Disable live trading
        </h3>
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
          New live orders will stop being placed. In-flight orders are
          NOT automatically cancelled. Use the kill switch to cancel
          in-flight orders immediately.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="rounded border border-slate-300 px-3 py-1 text-sm
              dark:border-slate-600"
            data-testid="live-disable-modal-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className="rounded bg-rose-600 px-3 py-1 text-sm text-white
              hover:bg-rose-700 disabled:opacity-50"
            data-testid="live-disable-modal-confirm"
          >
            {pending ? "Disabling…" : "Disable live orders"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function LiveModeToggle({
  strategyId,
  strategyName,
  onToggled,
}: Props) {
  const { gates, loading, error, revalidate } =
    useLiveStatus(strategyId);

  const [showEnable, setShowEnable] = useState(false);
  const [showDisable, setShowDisable] = useState(false);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const isEnabled = gates?.live_orders_enabled ?? false;
  const allPass = gates?.all_pass ?? false;

  async function handleEnable(confirmedName: string) {
    setPending(true);
    setActionError(null);
    try {
      await enableLiveOrders(strategyId, confirmedName);
      setShowEnable(false);
      await revalidate();
      onToggled?.();
    } catch (exc) {
      setActionError(
        exc instanceof Error ? exc.message : "Failed to enable",
      );
    } finally {
      setPending(false);
    }
  }

  async function handleDisable() {
    setPending(true);
    setActionError(null);
    try {
      await disableLiveOrders(strategyId);
      setShowDisable(false);
      await revalidate();
      onToggled?.();
    } catch (exc) {
      setActionError(
        exc instanceof Error ? exc.message : "Failed to disable",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div
        className={`rounded-md border p-3 ${
          isEnabled
            ? "border-indigo-300 bg-indigo-50 dark:border-indigo-700 dark:bg-indigo-950/20"
            : "border-slate-200 dark:border-slate-700"
        }`}
        data-testid="live-mode-toggle"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Live order placement
              {isEnabled && (
                <span
                  className="ml-2 rounded-full bg-indigo-100 px-2 py-0.5
                    text-[10px] font-medium text-indigo-700
                    dark:bg-indigo-950/60 dark:text-indigo-300"
                  data-testid="live-mode-enabled-chip"
                >
                  LIVE
                </span>
              )}
            </h3>
            <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
              {isEnabled
                ? "Real orders are being placed via Kite. Disable to stop."
                : "All 4 gates must pass before you can enable live trading."}
            </p>
          </div>

          {isEnabled ? (
            <button
              type="button"
              onClick={() => setShowDisable(true)}
              disabled={pending}
              className="flex-shrink-0 rounded bg-slate-700 px-3 py-1.5
                text-sm font-medium text-white hover:bg-slate-800
                disabled:opacity-60"
              data-testid="live-mode-disable-btn"
            >
              Disable
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setShowEnable(true)}
              disabled={!allPass || pending}
              title={
                !allPass
                  ? "One or more gates are closed — see checklist below"
                  : undefined
              }
              className="flex-shrink-0 rounded bg-indigo-600 px-3 py-1.5
                text-sm font-medium text-white hover:bg-indigo-700
                disabled:cursor-not-allowed disabled:opacity-40"
              data-testid="live-mode-enable-btn"
            >
              Enable live orders
            </button>
          )}
        </div>

        {/* Gate checklist */}
        {!loading && gates && (
          <ul
            className="mt-3 space-y-1"
            data-testid="live-gate-checklist"
          >
            {(
              Object.entries(GATE_LABELS) as [
                keyof typeof GATE_LABELS,
                string,
              ][]
            ).map(([key, label]) => (
              <GateBadge
                key={key}
                label={label}
                passed={gates[key]}
              />
            ))}
          </ul>
        )}

        {loading && (
          <p className="mt-2 text-xs text-slate-500">
            Checking gates…
          </p>
        )}

        {error && (
          <p
            className="mt-2 text-xs text-rose-600"
            data-testid="live-status-error"
          >
            {error}
          </p>
        )}
        {actionError && (
          <p
            className="mt-2 text-xs text-rose-600"
            data-testid="live-action-error"
          >
            {actionError}
          </p>
        )}
      </div>

      {showEnable && (
        <EnableConfirmModal
          strategyName={strategyName}
          onConfirm={handleEnable}
          onCancel={() => {
            setShowEnable(false);
            setActionError(null);
          }}
          pending={pending}
          error={actionError}
        />
      )}

      {showDisable && (
        <DisableConfirmModal
          onConfirm={handleDisable}
          onCancel={() => {
            setShowDisable(false);
            setActionError(null);
          }}
          pending={pending}
        />
      )}
    </>
  );
}
