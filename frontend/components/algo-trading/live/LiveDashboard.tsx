"use client";

import { useState } from "react";
import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { useStrategies } from "@/hooks/useStrategies";

import { AttributionPanel } from "../AttributionPanel";
import { LiveSafetyBeltsForm } from "../LiveSafetyBeltsForm";
import { RegimeHistoryChart } from "../RegimeHistoryChart";
import { RegimeWidget } from "../RegimeWidget";

import { OpenPositionsWidget } from "./OpenPositionsWidget";
import { PanicCloseButton } from "./PanicCloseButton";
import { RecentFillsTape } from "./RecentFillsTape";

interface DryRunState {
  dry_run: boolean;
}

async function fetchDryRun(url: string): Promise<DryRunState> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function panicCloseAll(): Promise<void> {
  const r = await apiFetch(
    `${API_URL}/algo/kill-switch/panic-close-all`,
    { method: "POST" },
  );
  if (!r.ok) {
    throw new Error(`panic-close-all HTTP ${r.status}`);
  }
}

/**
 * LiveDashboard — body of the Live → Live tab (rose accent).
 *
 * Layout: defensive dry-run banner → strategy picker + RegimeWidget
 * + PanicCloseButton row → 4-zone 2x2 grid (positions / regime /
 * active-strategy safety belts / fills tape) → collapsed
 * attribution drawer at the bottom.
 *
 * The strategy picker state drives both the safety belts form and
 * AttributionPanel (passing `strategyId || null` so neither
 * renders against an arbitrary default).
 */
export function LiveDashboard() {
  const { strategies } = useStrategies();
  const [strategyId, setStrategyId] = useState<string>("");

  // Defensive dry-run banner — should never show on this page,
  // but if the runtime is in rehearsal we surface it loudly.
  const { data: dry } = useSWR<DryRunState>(
    `${API_URL}/algo/live/dry-run`,
    fetchDryRun,
    { refreshInterval: 30_000, revalidateOnFocus: false },
  );

  return (
    <div className="space-y-3" data-testid="live-dashboard">
      {dry?.dry_run && (
        <div
          className="rounded-md border border-amber-300 bg-amber-50
            px-3 py-2 text-xs text-amber-800"
          data-testid="live-dryrun-warning"
        >
          Dry-run is armed. You are on the Live page; this banner
          means the runtime is in rehearsal mode. Disarm dry-run in
          Live → Settings to send real orders.
        </div>
      )}

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
          <AttributionPanel strategyId={strategyId || null} />
        </div>
      </details>
    </div>
  );
}
