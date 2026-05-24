"use client";

import { useState } from "react";
import { setAllocation } from "@/hooks/useBudget";
import type { UserBudgetView } from "@/lib/types/algoBudget";

interface Props {
  current: UserBudgetView;
  onClose: () => void;
  onSaved: () => void;
}

export function BudgetAllocationModal(
  { current, onClose, onSaved }: Props,
) {
  const [val, setVal] = useState(current.allocated_inr);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const committed =
    Number(current.open_pos_cost)
    + Number(current.active_reserved);
  const numVal = Number(val);
  const isInvalid =
    Number.isNaN(numVal)
    || numVal < 0;
  const belowCommitted = !isInvalid && numVal < committed;

  async function handleSave() {
    if (isInvalid) return;
    setSubmitting(true);
    setErr(null);
    try {
      await setAllocation(val);
      onSaved();
      onClose();
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? exc.message
          : "Save failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="budget-allocation-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-96 space-y-3">
        <h3 className="text-sm font-semibold">
          Edit Algo Budget Allocation
        </h3>
        {current.kite_available && (
          <p className="text-xs text-slate-500">
            Total Kite wallet:{" "}
            ₹{Number(
              current.kite_available,
            ).toLocaleString("en-IN")}
          </p>
        )}
        <label className="flex flex-col gap-1 text-xs">
          <span>Algo allocation (₹)</span>
          <input
            type="number"
            min={0}
            step={100}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            data-testid="budget-allocation-input"
            className="rounded border border-slate-300 dark:border-slate-600 px-2 py-1"
          />
        </label>
        {belowCommitted && (
          <p
            className="text-xs text-amber-700 dark:text-amber-300"
            data-testid="budget-allocation-below-committed-warning"
          >
            You currently have ₹
            {committed.toLocaleString("en-IN")} committed.
            Reducing below this means no new orders will
            fire until existing positions close.
          </p>
        )}
        {err && (
          <p className="text-xs text-rose-600">{err}</p>
        )}
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded border px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={isInvalid || submitting}
            data-testid="budget-allocation-save-button"
            className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
