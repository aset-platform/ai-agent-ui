"use client";

import { useState } from "react";
import { useSweepRun } from "@/hooks/useSweepRuns";

import { SweepEquityCurves } from "./SweepEquityCurves";
import { SweepForm } from "./SweepForm";
import { SweepPboBadge } from "./SweepPboBadge";
import { SweepProgressPanel } from "./SweepProgressPanel";
import { SweepResultsTable } from "./SweepResultsTable";

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
      {run && run.status === "completed" && (
        <>
          <SweepResultsTable run={run} />
          <SweepPboBadge run={run} />
          <SweepEquityCurves run={run} />
        </>
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
