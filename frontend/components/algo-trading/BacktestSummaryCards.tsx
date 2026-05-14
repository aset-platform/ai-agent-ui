"use client";

import type { BacktestSummary } from "@/hooks/useBacktestRuns";

interface Props {
  summary: BacktestSummary;
}

function fmtInr(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);
}

function fmtPct(v: string): string {
  return `${Number(v).toFixed(2)}%`;
}

// ASETPLTFRM-400 slice 7 — render the run's bar cadence as a small
// chip above the summary cards so users see at a glance whether the
// result came from the daily or intraday loader. Daily is the
// silent default; intraday cadences use an amber chip to make
// "this is intraday data" visually obvious (mirrors the stale-data
// chip pattern in §5.5 of CLAUDE.md).
function cadenceLabel(intervalSec: number | undefined): string {
  switch (intervalSec) {
    case 60:
      return "1m";
    case 300:
      return "5m";
    case 900:
      return "15m";
    case 86400:
    case undefined:
      return "Daily";
    default:
      return `${intervalSec}s`;
  }
}

function CadenceChip({ intervalSec }: { intervalSec?: number }) {
  const label = cadenceLabel(intervalSec);
  const isIntraday =
    intervalSec !== undefined && intervalSec !== 86400;
  const cls = isIntraday
    ? "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700"
    : "bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}
      data-testid="backtest-cadence-chip"
      title={
        isIntraday
          ? `Backtest ran at ${label} cadence (intraday loader)`
          : "Backtest ran at daily cadence"
      }
    >
      <span aria-hidden>⏱</span>
      Cadence: {label}
    </span>
  );
}

export function BacktestSummaryCards({ summary }: Props) {
  const positive = Number(summary.total_pnl_inr) >= 0;
  // Count trades that were force-closed at period end so the
  // summary can hint at why the trade table grew vs a strategy
  // that exited every position via its own signals.
  const mtmExits = summary.trade_list.filter(
    (t) => t.exit_reason === "period_end_mtm",
  ).length;
  const misExits = summary.trade_list.filter(
    (t) => t.exit_reason === "mis_square_off",
  ).length;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <CadenceChip intervalSec={summary.interval_sec} />
        {(mtmExits > 0 || misExits > 0) && (
          <div
            className="text-[11px] text-slate-500 dark:text-slate-400"
            data-testid="backtest-exit-summary"
          >
            {mtmExits > 0 && (
              <span title="Open positions at period end were force-closed at the last bar's close so Total PnL reconciles with the trade list.">
                {mtmExits} position{mtmExits === 1 ? "" : "s"} closed
                at period end (MTM)
              </span>
            )}
            {mtmExits > 0 && misExits > 0 && " · "}
            {misExits > 0 && (
              <span title="MIS strategies auto-square-off at the end of every trading day.">
                {misExits} MIS daily square-off
                {misExits === 1 ? "" : "s"}
              </span>
            )}
          </div>
        )}
      </div>
      <div
        className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6"
        data-testid="backtest-summary-cards"
      >
        <Card
          label="Total PnL"
          value={fmtInr(summary.total_pnl_inr)}
          tone={positive ? "good" : "bad"}
        />
        <Card
          label="PnL %"
          value={fmtPct(summary.total_pnl_pct)}
          tone={positive ? "good" : "bad"}
        />
        <Card label="Trades" value={String(summary.total_trades)} />
        <Card label="Win Rate" value={fmtPct(summary.win_rate_pct)} />
        <Card
          label="Max DD"
          value={fmtPct(summary.max_drawdown_pct)}
          tone="bad"
        />
        <Card label="Fees" value={fmtInr(summary.total_fees_inr)} />
      </div>
    </div>
  );
}

function Card({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const valueClass =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "bad"
        ? "text-rose-600 dark:text-rose-400"
        : "text-slate-900 dark:text-slate-100";
  const slug = label.toLowerCase().replace(/\s+/g, "-");
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2"
      data-testid={`backtest-card-${slug}`}
    >
      <div className="text-xs text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div className={`text-lg font-semibold ${valueClass}`}>
        {value}
      </div>
    </div>
  );
}
