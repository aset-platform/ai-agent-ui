"use client";

import { useState } from "react";

import { useKillSwitch } from "@/hooks/useKillSwitch";
import { useLiveCaps } from "@/hooks/useLiveCaps";
import { useLiveStatus } from "@/hooks/useLiveStatus";
import {
  usePaperEvents,
  type EventsMode,
} from "@/hooks/usePaperEvents";
import { useStrategies } from "@/hooks/useStrategies";

import { ActiveRunsPanel } from "./ActiveRunsPanel";
import { LiveCancelInFlightBanner } from "./LiveCancelInFlightBanner";
import { LiveDryRunBanner } from "./LiveDryRunBanner";
import { LiveLandedOrdersList } from "./LiveLandedOrdersList";
import { LiveModeToggle } from "./LiveModeToggle";
import { LiveSafetyBeltsForm } from "./LiveSafetyBeltsForm";
import { LiveWsHealthDot } from "./LiveWsHealthDot";
import {
  PaperEventsTimeline,
  type EventsPageSize,
} from "./PaperEventsTimeline";
import { ReconciliationDriftPanel } from "./ReconciliationDriftPanel";
import { KitePostbackPanel } from "./KitePostbackPanel";
import { AttributionPanel } from "./AttributionPanel";
import { RegimeWidget } from "./RegimeWidget";
import { RegimeHistoryChart } from "./RegimeHistoryChart";
import { RegimeChangeBanner } from "./RegimeChangeBanner";

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

const DEFAULT_EVENTS_PAGE_SIZE: EventsPageSize = 100;

type ViewMode = "live" | "paper" | "dryrun";

interface ViewFilter {
  mode: EventsMode;
  dryRun: boolean | null;
}

const VIEW_TO_FILTER: Record<ViewMode, ViewFilter> = {
  live:   { mode: "live",  dryRun: false },
  paper:  { mode: "paper", dryRun: null },
  dryrun: { mode: "live",  dryRun: true },
};

/** When the user clicks Dry run / Live, fire off the
 *  arm / disarm endpoint so the per-user Redis flag is in
 *  sync with their selection. Paper view doesn't touch the
 *  flag — it's irrelevant to PaperRuntime. */
async function setDryRunRedis(armed: boolean): Promise<void> {
  const path = armed
    ? "/algo/live/dry-run/arm"
    : "/algo/live/dry-run/disarm";
  try {
    const { apiFetch } = await import("@/lib/apiFetch");
    const { API_URL } = await import("@/lib/config");
    await apiFetch(`${API_URL}${path}`, { method: "POST" });
  } catch {
    // Best-effort — UI state is still set; backend resolution
    // falls back to env if the request failed.
  }
}

export function PaperTab() {
  // Live first per user request — the live trading is the
  // primary purpose of the page. Paper / Dry run are secondary
  // segments for development + rehearsal.
  const [viewMode, setViewMode] = useState<ViewMode>("live");

  const onChangeViewMode = (next: ViewMode) => {
    setViewMode(next);
    // Sync the per-user Redis dry-run flag with the segment.
    if (next === "dryrun") {
      void setDryRunRedis(true);
    } else if (next === "live") {
      void setDryRunRedis(false);
    }
  };
  const [eventsPage, setEventsPage] = useState(0);
  const [eventsPageSize, setEventsPageSize] = useState<EventsPageSize>(
    DEFAULT_EVENTS_PAGE_SIZE,
  );
  const filter = VIEW_TO_FILTER[viewMode];
  const { events, loading, error, total } = usePaperEvents(
    eventsPageSize,
    eventsPage * eventsPageSize,
    filter.mode,
    filter.dryRun,
  );

  // Reset to first page when mode toggle flips so the user
  // doesn't get stuck on a stale page index from the prior mode.
  const prevModeRef = (
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useState(viewMode)
  );
  if (prevModeRef[0] !== viewMode) {
    prevModeRef[1](viewMode);
    if (eventsPage !== 0) setEventsPage(0);
  }
  const { state: killState } = useKillSwitch();
  const { strategies } = useStrategies();

  // Selected strategy for the live section
  const [liveStrategyId, setLiveStrategyId] = useState<string>("");

  const selectedStrategy = strategies.find(
    (s) => s.id === liveStrategyId,
  );

  return (
    <div className="space-y-4" data-testid="paper-tab">
      <RegimeChangeBanner />
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              Trading
            </h2>
            <RegimeWidget />
          </div>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            Paper: replay-fixture runs against a synthetic broker.
            Dry run: live-mode rehearsal with synthetic Kite
            responses. Live: real Kite orders with safety belts.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Mode toggle — segregates the page into Paper / Live
              views and filters the events timeline accordingly. */}
          <div
            className="inline-flex rounded-md border border-slate-300
              dark:border-slate-600 overflow-hidden text-xs
              font-medium"
            role="tablist"
            aria-label="Trading mode"
            data-testid="trading-mode-toggle"
          >
            {([
              { id: "live", label: "Live", active: "bg-rose-600 text-white" },
              { id: "paper", label: "Paper", active: "bg-indigo-600 text-white" },
              { id: "dryrun", label: "Dry run", active: "bg-amber-500 text-white" },
            ] as const).map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={viewMode === tab.id}
                onClick={() => onChangeViewMode(tab.id)}
                className={
                  (viewMode === tab.id ? tab.active : (
                    "bg-white dark:bg-slate-800 text-slate-700 "
                    + "dark:text-slate-200 "
                    + "hover:bg-slate-50 dark:hover:bg-slate-700"
                  )) + " px-3 py-1.5"
                }
                data-testid={`trading-mode-${tab.id}`}
              >
                {tab.label}
              </button>
            ))}
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
      </div>

      {/* Reconciliation drift chip — relevant only in Live + Dry-run */}
      {(viewMode === "live" || viewMode === "dryrun") && (
        <ReconciliationDriftPanel />
      )}

      {viewMode === "paper" && (
        <div data-testid="trading-paper-view">
          <ActiveRunsPanel tradingMode="paper" />
        </div>
      )}

      {(viewMode === "live" || viewMode === "dryrun") && (
        <div className="space-y-4" data-testid="trading-live-view">
          <div
            className="rounded-md border border-indigo-100
              dark:border-indigo-900/50 p-3"
            data-testid="live-mode-section"
          >
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-slate-900
                dark:text-slate-100">
                Live order placement
              </h3>
              {/* OBS-1 — Kite WS health dot. Polls /ws-health
                  every 10s; tooltip shows tick count + age. */}
              <LiveWsHealthDot />
            </div>
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

          {/* Active runs starter — appears in BOTH dryrun and live
              views so the user has a single entry point to start
              the run. The panel adapts its source choices and
              backend mode= based on tradingMode prop. */}
          <ActiveRunsPanel tradingMode={viewMode} />

          {/* Kite postbacks — Live segment only, mounted AFTER
              ActiveRunsPanel so the active runs starter stays in
              view above the fold even as the postback list grows. */}
          {viewMode === "live" && (
            <div
              className="rounded-md border border-slate-200
                dark:border-slate-700"
              data-testid="live-postback-section"
            >
              <KitePostbackPanel />
            </div>
          )}

          {/* REGIME-6 — Attribution panel. Live segment only;
              the daily Brinson + trade reasons are scoped to the
              live trading flow. Renders even without a strategy
              selection (shows a guidance empty-state). */}
          {viewMode === "live" && (
            <AttributionPanel
              strategyId={liveStrategyId || null}
            />
          )}

          {/* Regime history chart — surfaces the rolling 252d
              regime ribbon + HMM stress line. Live + Dry-run only;
              paper mode is replay-fixture so historical regime
              context is less relevant there. */}
          <RegimeHistoryChart />
        </div>
      )}

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
        emptyMessage={
          viewMode === "paper"
            ? "No paper events yet. Start a paper run "
              + "(replay fixture) to see signals + fills here."
            : viewMode === "dryrun"
              ? "No dry-run events yet. Start a run from the "
                + "Active runs panel above. For markets-closed "
                + "rehearsal use the Replay fixture source — "
                + "Live Kite WS won't yield ticks on a weekend."
              : "No live events yet. Start a run with Live "
                + "Kite WS during market hours (09:15–15:30 "
                + "IST) to see real fills here."
        }
      />
    </div>
  );
}
