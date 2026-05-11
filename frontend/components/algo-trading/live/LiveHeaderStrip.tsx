"use client";

import { useLiveDashboardSummary } from "@/hooks/useLiveDashboardSummary";

import { LiveModeChip } from "./LiveModeChip";

function inr(value: string | undefined): string {
  if (value == null) return "₹0";
  const n = Number(value);
  if (!Number.isFinite(n)) return "₹0";
  return `₹${n.toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  })}`;
}

function signed(value: string | undefined): string {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n)) return "₹0";
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
 * `armed` here is a header-strip heuristic — the runtime has done
 * something today (open positions OR non-zero today P&L) — not a
 * read of `gates.live_orders_enabled`. The full chip wired up to
 * gates lives on the {@link LiveModeToggle} inside Live → Settings.
 */
export function LiveHeaderStrip() {
  const { summary } = useLiveDashboardSummary();
  const armed =
    (summary?.open_position_count ?? 0) > 0 ||
    (summary?.kill_switch_active === false &&
      Number(summary?.today_pnl_inr ?? 0) !== 0);

  const wsAge = summary?.ws_age_seconds;
  const wsOk = (wsAge ?? 999) < 10;

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
        <span
          className={`h-2 w-2 rounded-full ${
            wsOk ? "bg-emerald-500" : "bg-rose-500"
          }`}
        />
        <span>{wsAge != null ? `${wsAge}s` : "—"}</span>
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
