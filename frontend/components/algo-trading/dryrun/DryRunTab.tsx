"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { usePaperEvents } from "@/hooks/usePaperEvents";
import {
  filterStrategiesByMode,
  useStrategies,
} from "@/hooks/useStrategies";

import { ActiveRunsPanel } from "../ActiveRunsPanel";
import { AttributionPanel } from "../AttributionPanel";
import { LiveWsHealthDot } from "../LiveWsHealthDot";
import {
  PaperEventsTimeline,
  type EventsPageSize,
} from "../PaperEventsTimeline";
import { PaperSessionSummary } from "../PaperSessionSummary";
import { PromotionToLiveCallout } from "../PromotionToLiveCallout";
import { ReconciliationDriftPanel } from "../ReconciliationDriftPanel";
import { RegimeWidget } from "../RegimeWidget";

import { DryRunArmBanner } from "./DryRunArmBanner";

const DEFAULT_PAGE_SIZE: EventsPageSize = 100;

const DRY_RUN_KEY = `${API_URL}/algo/live/dry-run`;

async function fetchDryRunState(
  url: string,
): Promise<{ dry_run: boolean }> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function setDryRun(armed: boolean): Promise<void> {
  const path = armed
    ? `${API_URL}/algo/live/dry-run/arm`
    : `${API_URL}/algo/live/dry-run/disarm`;
  await apiFetch(path, { method: "POST" });
}

export function DryRunTab() {
  const { data, mutate } = useSWR<{ dry_run: boolean }>(
    DRY_RUN_KEY,
    fetchDryRunState,
    {
      revalidateOnFocus: false,
    },
  );
  const armed = data?.dry_run ?? false;

  const onToggleArm = useCallback(
    async (next: boolean) => {
      await mutate({ dry_run: next }, { revalidate: false });
      try {
        await setDryRun(next);
      } catch {
        // revalidate so the UI reflects the actual backend state
        await mutate();
      }
    },
    [mutate],
  );

  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<EventsPageSize>(
    DEFAULT_PAGE_SIZE,
  );
  const { events, loading, error, total } = usePaperEvents(
    pageSize,
    page * pageSize,
    "live",
    true, // dry_run filter
  );

  // Dry-run rehearses a paper-promoted strategy under live-runtime
  // plumbing — picker shows paper-only (mode-strict separation
  // per the promotion workflow rules).
  const { strategies: allStrategies } = useStrategies();
  const strategies = filterStrategiesByMode(allStrategies, [
    "paper",
  ]);

  return (
    <div className="space-y-4" data-testid="dryrun-tab">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              Dry Run
            </h2>
            <RegimeWidget />
          </div>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            Rehearses the live runtime with synthetic Kite responses.
            Use this to validate gating and order plumbing before
            going to Live Trading.
          </p>
        </div>
        <LiveWsHealthDot />
      </div>

      <DryRunArmBanner armed={armed} onToggle={onToggleArm} />

      <PromotionToLiveCallout surface="dryrun" />

      <ReconciliationDriftPanel />

      <ActiveRunsPanel tradingMode="dryrun" />

      {/* Kite postbacks intentionally not shown on Dry Run.
          Real Kite postbacks come from the exchange via webhook
          and only ever exist for real-money orders; synthetic
          dry-run fills are stamped directly into algo.events and
          never produce postbacks. Postbacks live on the Live
          page (?tab=postbacks). */}

      <AttributionPanel
        strategyId={strategies[0]?.id ?? null}
        mode="live"
        dryRun={true}
      />

      <PaperSessionSummary mode="live" dryRun={true} />

      {error && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3
            text-sm text-rose-700"
          data-testid="dryrun-events-error"
        >
          {error}
        </div>
      )}

      <PaperEventsTimeline
        events={events}
        loading={loading}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
        emptyMessage="No dry-run events yet. Arm dry-run, then start a run from the panel above."
      />
    </div>
  );
}
