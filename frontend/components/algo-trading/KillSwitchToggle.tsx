"use client";

import { useState } from "react";

import {
  armKillSwitch,
  disarmKillSwitch,
  useKillSwitch,
} from "@/hooks/useKillSwitch";

export function KillSwitchToggle() {
  const { state, loading, error } = useKillSwitch();
  const [pending, setPending] = useState(false);
  const [showArmConfirm, setShowArmConfirm] = useState(false);
  const [showDisarmConfirm, setShowDisarmConfirm] = useState(false);
  const [reason, setReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  if (loading) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-3 text-sm text-slate-500"
        data-testid="kill-switch-loading"
      >
        Loading kill switch state…
      </div>
    );
  }

  const isArmed = state?.active ?? false;

  async function handleArm() {
    setPending(true);
    setActionError(null);
    try {
      await armKillSwitch(reason.trim() || undefined);
      setShowArmConfirm(false);
      setReason("");
    } catch (exc) {
      setActionError(
        exc instanceof Error ? exc.message : "Failed to arm",
      );
    } finally {
      setPending(false);
    }
  }

  async function handleDisarm() {
    setPending(true);
    setActionError(null);
    try {
      await disarmKillSwitch();
      setShowDisarmConfirm(false);
    } catch (exc) {
      setActionError(
        exc instanceof Error ? exc.message : "Failed to disarm",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className={`rounded-md border p-3 ${
        isArmed
          ? "border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/30"
          : "border-slate-200 dark:border-slate-700"
      }`}
      data-testid="kill-switch-toggle"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Kill switch
          </h3>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            {isArmed
              ? "ARMED — all paper-trading signals are blocked."
              : "Inactive. Arm to immediately halt all paper-trading signal emission."}
          </p>
          {isArmed && state?.reason && (
            <p
              className="mt-1 text-xs italic text-rose-700 dark:text-rose-300"
              data-testid="kill-switch-reason"
            >
              Reason: {state.reason}
            </p>
          )}
        </div>
        {isArmed ? (
          <button
            type="button"
            onClick={() => setShowDisarmConfirm(true)}
            disabled={pending}
            className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
            data-testid="kill-switch-disarm-btn"
          >
            Disarm
          </button>
        ) : (
          <button
            type="button"
            onClick={() => setShowArmConfirm(true)}
            disabled={pending}
            className="rounded bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-60"
            data-testid="kill-switch-arm-btn"
          >
            Arm kill switch
          </button>
        )}
      </div>

      {error && (
        <p
          className="mt-2 text-xs text-rose-600"
          data-testid="kill-switch-load-error"
        >
          {error}
        </p>
      )}
      {actionError && (
        <p
          className="mt-2 text-xs text-rose-600"
          data-testid="kill-switch-action-error"
        >
          {actionError}
        </p>
      )}

      {showArmConfirm && (
        <div
          className="mt-3 rounded border border-rose-300 bg-white p-3 dark:bg-slate-900"
          data-testid="kill-switch-arm-confirm"
        >
          <p className="text-sm text-slate-900 dark:text-slate-100">
            Arming the kill switch will block ALL paper-trading
            signals until disarmed. Continue?
          </p>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Optional reason"
            maxLength={256}
            className="mt-2 w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
            data-testid="kill-switch-reason-input"
          />
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={handleArm}
              disabled={pending}
              className="rounded bg-rose-600 px-3 py-1 text-sm text-white disabled:opacity-60"
              data-testid="kill-switch-arm-confirm-btn"
            >
              {pending ? "Arming…" : "Yes, arm"}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowArmConfirm(false);
                setReason("");
              }}
              disabled={pending}
              className="rounded border border-slate-300 px-3 py-1 text-sm dark:border-slate-600"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {showDisarmConfirm && (
        <div
          className="mt-3 rounded border border-emerald-300 bg-white p-3 dark:bg-slate-900"
          data-testid="kill-switch-disarm-confirm"
        >
          <p className="text-sm text-slate-900 dark:text-slate-100">
            Disarming will allow paper-trading signals to resume.
            Continue?
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={handleDisarm}
              disabled={pending}
              className="rounded bg-emerald-600 px-3 py-1 text-sm text-white disabled:opacity-60"
              data-testid="kill-switch-disarm-confirm-btn"
            >
              {pending ? "Disarming…" : "Yes, disarm"}
            </button>
            <button
              type="button"
              onClick={() => setShowDisarmConfirm(false)}
              disabled={pending}
              className="rounded border border-slate-300 px-3 py-1 text-sm dark:border-slate-600"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
