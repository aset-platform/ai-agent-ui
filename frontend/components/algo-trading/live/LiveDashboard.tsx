"use client";

import { useState } from "react";

import { panicCloseAll } from "@/lib/algoApi";
import { useStrategies } from "@/hooks/useStrategies";

import { AttributionPanel } from "../AttributionPanel";
import { LiveSafetyBeltsForm } from "../LiveSafetyBeltsForm";
import { RegimeHistoryChart } from "../RegimeHistoryChart";
import { RegimeWidget } from "../RegimeWidget";

import { LiveActiveRunsPanel } from "./LiveActiveRunsPanel";
import { OpenPositionsWidget } from "./OpenPositionsWidget";
import { PanicCloseButton } from "./PanicCloseButton";
import { RecentFillsTape } from "./RecentFillsTape";

/**
 * LiveDashboard — body of the Live → Live tab (rose accent).
 *
 * Layout: LiveActiveRunsPanel (Start/Stop) → strategy picker +
 * RegimeWidget + PanicCloseButton row → 4-zone 2x2 grid
 * (positions / regime / active-strategy safety belts / fills tape)
 * → collapsed attribution drawer at the bottom.
 *
 * The strategy picker state drives both the safety belts form and
 * AttributionPanel (passing `strategyId || null` so neither
 * renders against an arbitrary default).
 *
 * ASETPLTFRM-374 (epic): no dry-run banner on this page. Live is
 * fully decoupled from the per-user dry-run Redis flag — any
 * runtime spawned from LiveActiveRunsPanel pins dry_run=False at
 * the API boundary (see backend routes/paper.py). Rehearsal mode
 * lives exclusively on Strategies → Dry-run tab.
 */
export function LiveDashboard() {
  const { strategies } = useStrategies();
  const [strategyId, setStrategyId] = useState<string>("");

  return (
    <div className="space-y-3" data-testid="live-dashboard">
      {/* ASETPLTFRM-378 — Start / Stop control for the Live runtime
          mounts at the top of this page (above OPEN POSITIONS).
          Previously users had to start the live runtime from the
          Strategies → Dry-run tab, which was conceptually wrong. */}
      <LiveActiveRunsPanel />

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

      {/* 4-zone grid */}
      <div className="grid gap-3 lg:grid-cols-2">
        {/* Zone A — Open positions compact */}
        <OpenPositionsWidget />

        {/* Zone B — Regime + stress mini chart */}
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
          data-testid="live-zone-b-regime"
        >
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Regime &amp; Stress
          </h3>
          <RegimeHistoryChart />
        </div>

        {/* Zone C — Active strategy safety belts */}
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

        {/* Zone D — Recent fills */}
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
