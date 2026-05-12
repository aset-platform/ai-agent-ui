"use client";

import { useEffect, useState } from "react";

import { panicCloseAll } from "@/lib/algoApi";
import { usePaperRuns } from "@/hooks/usePaperRuns";
import { useStrategies } from "@/hooks/useStrategies";

import { AttributionPanel } from "../AttributionPanel";
import { LiveSafetyBeltsForm } from "../LiveSafetyBeltsForm";
import { RegimeHistoryChart } from "../RegimeHistoryChart";
import { RegimeWidget } from "../RegimeWidget";

import { LiveActiveRunsPanel } from "./LiveActiveRunsPanel";
import { LiveEventsPanel } from "./LiveEventsPanel";
import { OpenPositionsWidget } from "./OpenPositionsWidget";
import { PanicCloseButton } from "./PanicCloseButton";
import { RecentFillsTape } from "./RecentFillsTape";

/**
 * LiveDashboard — body of the Live → Live tab (rose accent).
 *
 * Layout (top to bottom):
 *   1. Open Positions + Regime &amp; Stress (side-by-side row).
 *   2. Live runtime + Events feed (side-by-side row — same height).
 *   3. Strategy picker + RegimeWidget + PanicCloseButton.
 *   4. Active Strategy safety belts + Recent fills (2-col grid).
 *   5. Attribution drawer (collapsed).
 *
 * Single source of truth for `strategyId` lives here; passed down
 * to LiveActiveRunsPanel (Start picker), LiveSafetyBeltsForm, and
 * AttributionPanel so picking once is enough. Auto-defaults to the
 * first running live strategy or the first available strategy in
 * the list — the user only re-picks if they want to switch.
 *
 * ASETPLTFRM-374 (epic): no dry-run banner on this page. Live is
 * fully decoupled from the per-user dry-run Redis flag — any
 * runtime spawned from LiveActiveRunsPanel pins dry_run=False at
 * the API boundary (see backend routes/paper.py). Rehearsal mode
 * lives exclusively on Strategies → Dry-run tab.
 */
export function LiveDashboard() {
  const { strategies } = useStrategies();
  const { runs } = usePaperRuns();
  const [strategyId, setStrategyId] = useState<string>("");

  // Auto-default once strategies (and any running runs) load.
  // Priority: first live, non-dry-run running strategy → first
  // strategy in the list. Stops once the user has picked anything,
  // even if they later switch to "".
  useEffect(() => {
    if (strategyId) return;
    const liveRun = runs.find(
      (r) => r.mode === "live" && !r.dry_run,
    );
    if (liveRun) {
      setStrategyId(liveRun.strategy_id);
      return;
    }
    if (strategies.length > 0) {
      setStrategyId(strategies[0].id);
    }
  }, [strategyId, runs, strategies]);

  return (
    <div className="space-y-3" data-testid="live-dashboard">
      {/* Row 1 — Open Positions + Regime & Stress. */}
      <div className="grid gap-3 lg:grid-cols-2">
        <OpenPositionsWidget />
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
          data-testid="live-zone-b-regime"
        >
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Regime &amp; Stress
          </h3>
          <RegimeHistoryChart />
        </div>
      </div>

      {/* Row 2 — Live runtime Start/Stop + Events feed.
          ASETPLTFRM-378: per-page Start UI lives here; Events
          panel surfaces signals + order outcomes in real time so
          testers don't need to leave the page. Same-height row
          via auto-stretch + Events panel's h-full inner flex. */}
      <div className="grid gap-3 lg:grid-cols-2 items-stretch">
        <LiveActiveRunsPanel
          strategyId={strategyId}
          onStrategyChange={setStrategyId}
        />
        <LiveEventsPanel />
      </div>

      {/* Row 3 — Strategy picker + Regime + Panic Close. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <select
          className="rounded border border-slate-300 dark:border-slate-600
            bg-white dark:bg-slate-800 px-2 py-1 text-sm w-64"
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          data-testid="live-strategy-select"
        >
          <option value="">Select strategy…</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <div className="flex items-center gap-2">
          <RegimeWidget />
          <PanicCloseButton onConfirm={panicCloseAll} />
        </div>
      </div>

      {/* Row 4 — Active Strategy safety belts + Recent fills. */}
      <div className="grid gap-3 lg:grid-cols-2">
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
          data-testid="live-zone-c-strategy"
        >
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Active Strategy
          </h3>
          {strategyId ? (
            <LiveSafetyBeltsForm strategyId={strategyId} />
          ) : (
            <p className="mt-2 text-xs text-slate-400">
              Pick a strategy above to see safety belts.
            </p>
          )}
        </div>
        <RecentFillsTape />
      </div>

      {/* Footer — collapsed by default */}
      <details
        className="rounded-md border border-slate-200 dark:border-slate-700"
        data-testid="live-attribution-details"
      >
        <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Attribution
        </summary>
        <div className="border-t border-slate-200 dark:border-slate-700 p-3">
          <AttributionPanel
            strategyId={strategyId || null}
            mode="live"
            dryRun={false}
          />
        </div>
      </details>
    </div>
  );
}
