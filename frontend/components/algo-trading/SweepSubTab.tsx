"use client";

import { useState } from "react";
import { useSweepRun } from "@/hooks/useSweepRuns";

export function SweepSubTab() {
  const [activeSweepId, setActiveSweepId] = useState<
    string | null
  >(null);
  const { run } = useSweepRun(activeSweepId);

  const isDone =
    run?.status === "completed"
    || run?.status === "failed";

  return (
    <div className="space-y-4" data-testid="sweep-sub-tab">
      {/* Placeholder shell — child components arrive in
          Tasks 8-9. For now we render a stub so the
          parent + routing work end-to-end. */}
      {(activeSweepId == null || isDone) && (
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
          data-testid="sweep-form-placeholder"
        >
          Parameter sweep form coming in next slice.
          {activeSweepId && (
            <p className="mt-2 text-xs">
              Last sweep status: {run?.status}
            </p>
          )}
        </div>
      )}
      {activeSweepId && !isDone && (
        <div
          className="rounded-md border p-4 text-sm"
          data-testid="sweep-progress-placeholder"
        >
          Sweep in progress (id: {activeSweepId}).
        </div>
      )}
      {/* setActiveSweepId is reserved for next slice */}
      <button
        type="button"
        className="hidden"
        data-testid="sweep-set-active-id-stub"
        onClick={() => setActiveSweepId("test-id")}
      >
        stub
      </button>
    </div>
  );
}
