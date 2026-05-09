"use client";

import { useState } from "react";

import { useKillSwitch } from "@/hooks/useKillSwitch";
import { useLiveCaps } from "@/hooks/useLiveCaps";
import { useLiveStatus } from "@/hooks/useLiveStatus";
import { usePaperEvents } from "@/hooks/usePaperEvents";
import { useStrategies } from "@/hooks/useStrategies";

import { ActiveRunsPanel } from "./ActiveRunsPanel";
import { LiveCancelInFlightBanner } from "./LiveCancelInFlightBanner";
import { LiveDryRunBanner } from "./LiveDryRunBanner";
import { LiveLandedOrdersList } from "./LiveLandedOrdersList";
import { LiveModeToggle } from "./LiveModeToggle";
import { LiveSafetyBeltsForm } from "./LiveSafetyBeltsForm";
import { PaperEventsTimeline } from "./PaperEventsTimeline";
import { ReconciliationDriftPanel } from "./ReconciliationDriftPanel";

/** Live section for a specific strategy. */
function LiveSection({ strategyId, strategyName }: {
  strategyId: string;
  strategyName: string;
}) {
  const { caps } = useLiveCaps(strategyId);
  const { gates } = useLiveStatus(strategyId);
  const liveEnabled = caps?.live_orders_enabled ?? false;

  return (
    <div className="space-y-3" data-testid="live-section">
      {/* Dry-run mode amber banner — shown at top of live section */}
      <LiveDryRunBanner gates={gates} />

      {/* Kill-switch banner — only visible when live + kill armed */}
      <LiveCancelInFlightBanner liveEnabled={liveEnabled} />

      {/* 4-gate toggle */}
      <LiveModeToggle
        strategyId={strategyId}
        strategyName={strategyName}
      />

      {/* Safety belts caps form */}
      <div
        className="rounded-md border border-slate-200
          dark:border-slate-700 p-3"
        data-testid="live-safety-belts-panel"
      >
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide
          text-slate-500 dark:text-slate-400">
          Safety belts (caps)
        </h4>
        <LiveSafetyBeltsForm strategyId={strategyId} />
      </div>

      {/* In-flight orders (only shown when live is enabled) */}
      {liveEnabled && (
        <div
          className="rounded-md border border-slate-200
            dark:border-slate-700 p-3"
          data-testid="live-in-flight-panel"
        >
          <h4 className="mb-2 text-xs font-semibold uppercase
            tracking-wide text-slate-500 dark:text-slate-400">
            In-flight orders
          </h4>
          <LiveLandedOrdersList strategyId={strategyId} />
        </div>
      )}
    </div>
  );
}

const EVENTS_PAGE_SIZE = 100;

export function PaperTab() {
  const [eventsPage, setEventsPage] = useState(1);
  const { events, loading, error, hasMore } = usePaperEvents(
    EVENTS_PAGE_SIZE,
    (eventsPage - 1) * EVENTS_PAGE_SIZE,
  );
  const { state: killState } = useKillSwitch();
  const { strategies } = useStrategies();

  // Selected strategy for the live section
  const [liveStrategyId, setLiveStrategyId] = useState<string>("");

  const selectedStrategy = strategies.find(
    (s) => s.id === liveStrategyId,
  );

  return (
    <div className="space-y-4" data-testid="paper-tab">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
            Paper & live trading
          </h2>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            Paper: replay-fixture runs. Live: real Kite orders with
            safety belts.
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

      {/* Reconciliation drift chip */}
      <ReconciliationDriftPanel />

      {/* Controls row: Paper runs (left) + Live order placement (right).
          Stacks vertically on narrow viewports, side-by-side on lg+. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Paper runs */}
        <ActiveRunsPanel />

        {/* ---- Live mode section ---- */}
        <div
          className="rounded-md border border-indigo-100
            dark:border-indigo-900/50 p-3"
          data-testid="live-mode-section"
        >
          <h3 className="text-sm font-semibold text-slate-900
            dark:text-slate-100">
            Live order placement (V2-5)
          </h3>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            Select a strategy to configure and enable live order
            placement via Zerodha Kite.
          </p>

          <label className="mt-2 flex flex-col gap-0.5">
            <span className="text-[11px] text-slate-500">
              Strategy
            </span>
            <select
              className="rounded border border-slate-300
                dark:border-slate-600 bg-white dark:bg-slate-800
                px-2 py-1 text-sm w-64"
              value={liveStrategyId}
              onChange={(e) => setLiveStrategyId(e.target.value)}
              data-testid="live-strategy-select"
            >
              <option value="">Select strategy…</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>

          {liveStrategyId && selectedStrategy && (
            <div className="mt-3">
              <LiveSection
                strategyId={liveStrategyId}
                strategyName={selectedStrategy.name}
              />
            </div>
          )}

          {!liveStrategyId && (
            <p
              className="mt-3 text-xs text-slate-400"
              data-testid="live-no-strategy-msg"
            >
              Pick a strategy above to see live trading controls.
            </p>
          )}
        </div>
      </div>

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
        pageSize={EVENTS_PAGE_SIZE}
        hasMore={hasMore}
        onPageChange={setEventsPage}
      />
    </div>
  );
}
