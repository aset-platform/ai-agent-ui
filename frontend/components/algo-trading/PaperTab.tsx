"use client";

import { useKillSwitch } from "@/hooks/useKillSwitch";
import { usePaperEvents } from "@/hooks/usePaperEvents";

import { PaperEventsTimeline } from "./PaperEventsTimeline";

export function PaperTab() {
  const { events, loading, error } = usePaperEvents(100);
  const { state: killState } = useKillSwitch();

  return (
    <div className="space-y-4" data-testid="paper-tab">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
            Paper trading
          </h2>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            Read-only view of recent paper signals + fills.
            Multi-strategy supervisor + per-strategy run controls
            land in a future slice.
          </p>
        </div>
        {killState?.active && (
          <span
            className="rounded-full bg-rose-100 px-3 py-1 text-xs font-medium text-rose-800 dark:bg-rose-950/50 dark:text-rose-200"
            data-testid="paper-tab-kill-armed-chip"
          >
            Kill switch ARMED
          </span>
        )}
      </div>

      {error && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="paper-events-error"
        >
          {error}
        </div>
      )}

      <PaperEventsTimeline events={events} loading={loading} />
    </div>
  );
}
