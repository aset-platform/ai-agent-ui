"use client";

import { useState } from "react";

import { useKillSwitch } from "@/hooks/useKillSwitch";
import { usePaperEvents } from "@/hooks/usePaperEvents";
import {
  filterStrategiesByMode,
  useStrategies,
} from "@/hooks/useStrategies";

import { ActiveRunsPanel } from "./ActiveRunsPanel";
import { AttributionPanel } from "./AttributionPanel";
import {
  PaperEventsTimeline,
  type EventsPageSize,
} from "./PaperEventsTimeline";
import { PaperSessionSummary } from "./PaperSessionSummary";
import { PromotionToLiveCallout } from "./PromotionToLiveCallout";

const DEFAULT_EVENTS_PAGE_SIZE: EventsPageSize = 100;

export function PaperTab() {
  const [eventsPage, setEventsPage] = useState(0);
  const [eventsPageSize, setEventsPageSize] =
    useState<EventsPageSize>(DEFAULT_EVENTS_PAGE_SIZE);

  const { events, loading, error, total } = usePaperEvents(
    eventsPageSize,
    eventsPage * eventsPageSize,
    "paper",        // mode filter
    null,           // dryRun filter — irrelevant for paper mode
  );

  const { state: killState } = useKillSwitch();
  // Paper-tab picker shows only paper-stage strategies — strict
  // separation per the promotion workflow (live strategies live
  // on the Live tab only).
  const { strategies: allStrategies } = useStrategies();
  const strategies = filterStrategiesByMode(allStrategies, [
    "paper",
  ]);

  return (
    <div className="space-y-4" data-testid="paper-tab">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
            Paper Trading
          </h2>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            Replay-fixture runs against a synthetic broker. Use this
            tab to validate strategy logic without touching the live
            Kite runtime.
          </p>
        </div>
        {killState?.active && (
          <span
            className="rounded-full bg-rose-100 px-3 py-1 text-xs
              font-medium text-rose-800 dark:bg-rose-950/50
              dark:text-rose-200"
            data-testid="paper-tab-kill-armed-chip"
          >
            Kill switch ARMED
          </span>
        )}
      </div>

      <PromotionToLiveCallout surface="paper" />

      <ActiveRunsPanel tradingMode="paper" />
      <PaperSessionSummary />

      {/* Attribution scoped to paper-runtime fills only. Same
          component as Live/Dry-run; the mode="paper" filter on
          /algo/attribution/trades restricts to events.mode='paper'
          and type='order_filled', so dry-run + live + backtest
          fills don't bleed in. */}
      <AttributionPanel
        strategyId={strategies[0]?.id ?? null}
        mode="paper"
      />

      {error && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3
            text-sm text-rose-700"
          data-testid="paper-events-error"
        >
          {error}
        </div>
      )}

      <PaperEventsTimeline
        events={events}
        loading={loading}
        page={eventsPage}
        pageSize={eventsPageSize}
        total={total}
        onPageChange={setEventsPage}
        onPageSizeChange={setEventsPageSize}
        emptyMessage="No paper events yet. Start a paper run (replay fixture) to see signals + fills here."
      />
    </div>
  );
}
