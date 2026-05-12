"use client";

import { useLiveDashboardSummary } from "@/hooks/useLiveDashboardSummary";
import { usePaperRuns } from "@/hooks/usePaperRuns";

import { LiveWsHealthDot } from "../LiveWsHealthDot";
import { LiveModeChip } from "./LiveModeChip";

function inr(value: string | undefined): string {
  if (value == null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `₹${n.toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  })}`;
}

function signed(value: string | undefined): string {
  if (value == null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const prefix = n >= 0 ? "+" : "";
  return `${prefix}${inr(value)}`;
}

/**
 * Sticky KPI strip rendered above every Live tab.
 *
 * Pulls {@link useLiveDashboardSummary} (15 s refresh) and shows:
 * mode chip · today P&L · open P&L · realised P&L · cash ·
 * open positions count · WebSocket age dot.
 *
 * `armed` reflects "live runtime is running right now for me" —
 * read directly from {@link usePaperRuns} filtered to
 * mode=live && !dry_run. ASETPLTFRM-378 replaced the previous
 * "activity today" heuristic (open positions OR non-zero today
 * P&L) with this truthful signal: a fresh start of the day shows
 * ARMED the moment the user clicks Start on the new
 * LiveActiveRunsPanel, not only after the first fill.
 */
export function LiveHeaderStrip() {
  const { summary } = useLiveDashboardSummary();
  const { runs } = usePaperRuns();
  const armed = runs.some(
    (r) => r.mode === "live" && !r.dry_run,
  );

  return (
    <div
      className="sticky top-0 z-10 flex flex-wrap items-center gap-3
        bg-white/95 dark:bg-slate-900/95 backdrop-blur border-b
        border-slate-200 dark:border-slate-700 px-4 py-3"
      data-testid="live-header-strip"
    >
      <LiveModeChip
        mode={summary?.mode ?? "live"}
        armed={armed}
      />
      <Kpi label="Today P&L" value={signed(summary?.today_pnl_inr)} />
      <Kpi label="Open P&L" value={signed(summary?.open_pnl_inr)} />
      <Kpi
        label="Realised"
        value={signed(summary?.realised_pnl_inr)}
      />
      <Kpi label="Cash" value={inr(summary?.cash_inr)} />
      <Kpi
        label="Open"
        value={String(summary?.open_position_count ?? 0)}
      />
      <div
        className="flex items-center gap-1 text-xs text-slate-500"
        data-testid="live-ws-age"
      >
        <span>WS</span>
        <LiveWsHealthDot />
      </div>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        {value}
      </span>
    </div>
  );
}
