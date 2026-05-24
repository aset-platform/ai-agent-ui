"use client";

import { useState } from "react";
import { useSweepRun } from "@/hooks/useSweepRuns";

import { SweepForm } from "./SweepForm";
import { SweepProgressPanel } from "./SweepProgressPanel";

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
      {(activeSweepId == null || isDone) && (
        <SweepForm onStarted={setActiveSweepId} />
      )}
      {activeSweepId && !isDone && (
        <SweepProgressPanel sweepRunId={activeSweepId} />
      )}
      {/* Results UI (Block A/B/C) arrives in Task 9 — for
          now, when run.status === "completed" we just show
          a status line. */}
      {run && run.status === "completed" && (
        <div
          className="rounded-md border border-emerald-200 bg-emerald-50 dark:bg-emerald-950/30 p-4 text-sm"
          data-testid="sweep-results-placeholder"
        >
          Sweep complete. Results table + PBO badge arrive
          in next slice.
        </div>
      )}
      {run && run.status === "failed" && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700"
          data-testid="sweep-failed-state"
        >
          Sweep failed: {run.error_text ?? "unknown error"}
        </div>
      )}
    </div>
  );
}
