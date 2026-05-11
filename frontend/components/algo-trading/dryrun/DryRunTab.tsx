"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { usePaperEvents } from "@/hooks/usePaperEvents";
import { useStrategies } from "@/hooks/useStrategies";

import { ActiveRunsPanel } from "../ActiveRunsPanel";
import { AttributionPanel } from "../AttributionPanel";
import { KitePostbackPanel } from "../KitePostbackPanel";
import { LiveWsHealthDot } from "../LiveWsHealthDot";
import {
  PaperEventsTimeline,
  type EventsPageSize,
} from "../PaperEventsTimeline";
import { PaperSessionSummary } from "../PaperSessionSummary";
import { ReconciliationDriftPanel } from "../ReconciliationDriftPanel";
import { RegimeHistoryChart } from "../RegimeHistoryChart";
import { RegimeWidget } from "../RegimeWidget";

import { DryRunArmBanner } from "./DryRunArmBanner";

const DEFAULT_PAGE_SIZE: EventsPageSize = 100;

const DRY_RUN_KEY = `${API_URL}/algo/live/dry-run`;

async function fetchDryRunState(url: string): Promise<{ armed: boolean }> {
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
  const { data, mutate } = useSWR(DRY_RUN_KEY, fetchDryRunState, {
    revalidateOnFocus: false,
  });
  const armed = data?.armed ?? false;

  const onToggleArm = useCallback(
    async (next: boolean) => {
      await mutate({ armed: next }, false);
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

  const { strategies } = useStrategies();

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

      <ReconciliationDriftPanel />

      <ActiveRunsPanel tradingMode="dryrun" />

      <KitePostbackPanel />

      <AttributionPanel strategyId={strategies[0]?.id ?? null} />

      <RegimeHistoryChart />

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
