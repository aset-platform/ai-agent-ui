"use client";
/**
 * Per-strategy paper-trading P&L summary + open positions.
 *
 * Paper sessions don't write a row to algo.runs (only events),
 * so the Performance tab can't show them. This card synthesises
 * the same view from algo.events: FIFO-matched realised P&L per
 * ticker + open positions marked to the latest close.
 *
 * Polls every 10s via usePaperSessionSummary so an active
 * session's totals update without a manual refresh.
 */

import { useState } from "react";

import {
  usePaperSessionSummary,
  type OpenPosition,
} from "@/hooks/usePaperSessionSummary";
import { useStrategies } from "@/hooks/useStrategies";

function fmtINR(v: number): string {
  const sign = v < 0 ? "-" : "";
  return `${sign}₹${Math.abs(v).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function pnlClass(v: number): string {
  if (v > 0) return "text-emerald-600 dark:text-emerald-400";
  if (v < 0) return "text-rose-600 dark:text-rose-400";
  return "text-slate-600 dark:text-slate-400";
}

function OpenPositionsTable({ rows }: { rows: OpenPosition[] }) {
  if (rows.length === 0) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="paper-open-positions-empty"
      >
        No open positions.
      </p>
    );
  }
  return (
    <div
      className="overflow-x-auto rounded border border-slate-200
        dark:border-slate-700"
      data-testid="paper-open-positions-table"
    >
      <table className="min-w-full text-xs">
        <thead className="bg-slate-50 dark:bg-slate-800/60">
          <tr className="text-slate-600 dark:text-slate-300">
            <th className="px-2 py-1.5 text-left font-medium">
              Ticker
            </th>
            <th className="px-2 py-1.5 text-right font-medium">
              Qty
            </th>
            <th className="px-2 py-1.5 text-right font-medium">
              Avg ₹
            </th>
            <th className="px-2 py-1.5 text-right font-medium">
              Mark ₹
            </th>
            <th className="px-2 py-1.5 text-right font-medium">
              Unrealised
            </th>
            <th className="px-2 py-1.5 text-right font-medium">
              %
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.ticker}
              className="border-t border-slate-200
                dark:border-slate-700"
              data-testid={`paper-open-position-${r.ticker}`}
            >
              <td className="px-2 py-1 font-medium text-slate-900
                dark:text-slate-100">
                {r.ticker}
              </td>
              <td className="px-2 py-1 text-right">{r.qty}</td>
              <td className="px-2 py-1 text-right">
                {r.avg_price.toFixed(2)}
              </td>
              <td className="px-2 py-1 text-right">
                {r.last_price !== null
                  ? r.last_price.toFixed(2)
                  : "—"}
              </td>
              <td
                className={
                  "px-2 py-1 text-right "
                  + pnlClass(r.unrealised_pnl_inr)
                }
              >
                {fmtINR(r.unrealised_pnl_inr)}
              </td>
              <td
                className={
                  "px-2 py-1 text-right "
                  + pnlClass(r.unrealised_pnl_pct)
                }
              >
                {r.unrealised_pnl_pct.toFixed(2)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ClosedPositionsTable({
  rows,
}: {
  rows: { ticker: string; realised_pnl_inr: number; round_trips: number }[];
}) {
  if (rows.length === 0) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="paper-closed-positions-empty"
      >
        No closed round-trips yet.
      </p>
    );
  }
  // Sort by absolute P&L magnitude desc so winners + losers
  // surface above tiny near-zero noise.
  const sorted = [...rows].sort(
    (a, b) =>
      Math.abs(b.realised_pnl_inr) - Math.abs(a.realised_pnl_inr),
  );
  return (
    <div
      className="overflow-x-auto rounded border border-slate-200
        dark:border-slate-700"
      data-testid="paper-closed-positions-table"
    >
      <table className="min-w-full text-xs">
        <thead className="bg-slate-50 dark:bg-slate-800/60">
          <tr className="text-slate-600 dark:text-slate-300">
            <th className="px-2 py-1.5 text-left font-medium">
              Ticker
            </th>
            <th className="px-2 py-1.5 text-right font-medium">
              Round-trips
            </th>
            <th className="px-2 py-1.5 text-right font-medium">
              Realised
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr
              key={r.ticker}
              className="border-t border-slate-200
                dark:border-slate-700"
              data-testid={`paper-closed-position-${r.ticker}`}
            >
              <td className="px-2 py-1 font-medium text-slate-900
                dark:text-slate-100">
                {r.ticker}
              </td>
              <td className="px-2 py-1 text-right">
                {r.round_trips}
              </td>
              <td
                className={
                  "px-2 py-1 text-right "
                  + pnlClass(r.realised_pnl_inr)
                }
              >
                {fmtINR(r.realised_pnl_inr)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PaperSessionSummary() {
  const { strategies } = useStrategies();
  const [strategyId, setStrategyId] = useState<string>("");
  const { summary, loading, error } =
    usePaperSessionSummary(strategyId);

  return (
    <div
      className="rounded-md border border-slate-200
        dark:border-slate-700 p-3 space-y-3"
      data-testid="paper-session-summary"
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900
          dark:text-slate-100">
          Paper P&L by strategy
        </h3>
        <select
          className="rounded border border-slate-300
            dark:border-slate-600 bg-white dark:bg-slate-800
            px-2 py-1 text-xs w-64"
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          data-testid="paper-summary-strategy-select"
        >
          <option value="">Select strategy…</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>

      {!strategyId && (
        <p
          className="text-xs text-slate-500"
          data-testid="paper-summary-no-strategy"
        >
          Pick a strategy above to see paper-mode P&L.
        </p>
      )}

      {strategyId && loading && (
        <p
          className="text-xs text-slate-500"
          data-testid="paper-summary-loading"
        >
          Loading paper P&L…
        </p>
      )}

      {strategyId && error && (
        <p
          className="text-xs text-rose-600 dark:text-rose-400"
          data-testid="paper-summary-error"
        >
          {error.message}
        </p>
      )}

      {strategyId && summary && (
        <>
          {/* P&L cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div
              className="rounded border border-slate-200
                dark:border-slate-700 p-2"
              data-testid="paper-summary-realised"
            >
              <div className="text-[11px] uppercase tracking-wide
                text-slate-500">
                Realised
              </div>
              <div
                className={
                  "text-base font-semibold "
                  + pnlClass(summary.total_realised_pnl_inr)
                }
              >
                {fmtINR(summary.total_realised_pnl_inr)}
              </div>
            </div>
            <div
              className="rounded border border-slate-200
                dark:border-slate-700 p-2"
              data-testid="paper-summary-unrealised"
            >
              <div className="text-[11px] uppercase tracking-wide
                text-slate-500">
                Unrealised
              </div>
              <div
                className={
                  "text-base font-semibold "
                  + pnlClass(summary.total_unrealised_pnl_inr)
                }
              >
                {fmtINR(summary.total_unrealised_pnl_inr)}
              </div>
            </div>
            <div
              className="rounded border border-slate-200
                dark:border-slate-700 p-2"
              data-testid="paper-summary-total"
            >
              <div className="text-[11px] uppercase tracking-wide
                text-slate-500">
                Total
              </div>
              <div
                className={
                  "text-base font-semibold "
                  + pnlClass(summary.total_pnl_inr)
                }
              >
                {fmtINR(summary.total_pnl_inr)}
              </div>
            </div>
            <div
              className="rounded border border-slate-200
                dark:border-slate-700 p-2"
              data-testid="paper-summary-fills"
            >
              <div className="text-[11px] uppercase tracking-wide
                text-slate-500">
                Activity
              </div>
              <div className="text-xs text-slate-700 dark:text-slate-200
                pt-0.5">
                <span className="font-semibold">
                  {summary.n_fills}
                </span>{" "}
                fills · {summary.n_signals_generated} signals
              </div>
              <div className="text-[11px] text-slate-500">
                {summary.n_signals_rejected} rejected
              </div>
            </div>
          </div>

          {/* Open positions */}
          <div>
            <h4 className="mb-1.5 text-xs font-semibold uppercase
              tracking-wide text-slate-500 dark:text-slate-400">
              Open positions ({summary.open_positions.length})
            </h4>
            <OpenPositionsTable rows={summary.open_positions} />
          </div>

          {/* Closed positions */}
          <div>
            <h4 className="mb-1.5 text-xs font-semibold uppercase
              tracking-wide text-slate-500 dark:text-slate-400">
              Closed (FIFO-matched) ·{" "}
              {summary.closed_positions.length} tickers
            </h4>
            <ClosedPositionsTable
              rows={summary.closed_positions}
            />
          </div>

          {/* Top rejection reasons (if any) */}
          {Object.keys(summary.rejection_reasons).length > 0 && (
            <div className="text-[11px] text-slate-500
              dark:text-slate-400">
              <span className="font-medium">Rejections:</span>{" "}
              {Object.entries(summary.rejection_reasons)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([reason, n]) => `${reason} (${n})`)
                .join(" · ")}
            </div>
          )}
        </>
      )}
    </div>
  );
}
