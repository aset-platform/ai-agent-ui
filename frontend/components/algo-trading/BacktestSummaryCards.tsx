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

export function BacktestSummaryCards({ summary }: Props) {
  const positive = Number(summary.total_pnl_inr) >= 0;
  return (
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
