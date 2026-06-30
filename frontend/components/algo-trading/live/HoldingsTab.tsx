"use client";

import { useMemo } from "react";

import { useLiveHoldings } from "@/hooks/useLiveHoldings";

// ── Formatters ────────────────────────────────────────────────────

function fmtInr(v: string | number | null | undefined): string {
  if (v == null) return "—";
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return "—";
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function fmtSigned(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v).toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  });
  return `${v >= 0 ? "+" : "−"}₹${abs}`;
}

function fmtPct(v: string | number | null | undefined): string {
  if (v == null) return "—";
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function pnlClass(v: number): string {
  if (!Number.isFinite(v)) return "";
  return v >= 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}

// ── Scorecard ─────────────────────────────────────────────────────

function ScorecardCell({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-slate-500 dark:text-slate-400">
        {label}
      </span>
      <span
        className={`text-xl font-semibold tabular-nums text-slate-900
          dark:text-slate-100 ${valueClass ?? ""}`}
      >
        {value}
        {sub && (
          <span className="ml-2 text-sm font-medium">{sub}</span>
        )}
      </span>
    </div>
  );
}

// ── HoldingsTab ───────────────────────────────────────────────────

/**
 * HoldingsTab — T+1 CNC holdings with days-held + strategy join.
 *
 * Data source: {@link useLiveHoldings} (`/algo/live/holdings`).
 * `days_held` is computed backend-side from the earliest open
 * order in the ledger for that ticker; "—" means the holding is
 * either unattributed or older than the ledger horizon.
 */
export function HoldingsTab() {
  const { rows, loading, error } = useLiveHoldings();

  const totals = useMemo(() => {
    if (!rows || rows.length === 0) return null;
    let invested = 0;
    let current = 0;
    let pnl = 0;
    for (const r of rows) {
      const qty = r.quantity;
      const avg = Number(r.average_price);
      const ltp = Number(r.last_price);
      const p = Number(r.pnl_inr);
      if (qty > 0) {
        invested += qty * (Number.isFinite(avg) ? avg : 0);
        current += qty * (Number.isFinite(ltp) ? ltp : 0);
      }
      if (Number.isFinite(p)) pnl += p;
    }
    const pnlPct = invested > 0 ? (pnl / invested) * 100 : 0;
    return { invested, current, pnl, pnlPct };
  }, [rows]);

  if (loading) {
    return (
      <p
        className="text-sm text-slate-500"
        data-testid="holdings-loading"
      >
        Loading…
      </p>
    );
  }
  if (error) {
    return (
      <p
        className="text-sm text-rose-700"
        data-testid="holdings-error"
      >
        Could not load holdings: {String(error)}
      </p>
    );
  }
  if (!rows || rows.length === 0) {
    return (
      <p
        className="text-sm text-slate-500"
        data-testid="holdings-empty"
      >
        No holdings.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {/* Scorecard */}
      {totals && (
        <div
          className="grid grid-cols-3 gap-4 rounded-lg border
            border-slate-200 bg-slate-50 p-4
            dark:border-slate-700 dark:bg-slate-800/50"
          data-testid="holdings-scorecard"
        >
          <ScorecardCell
            label="Total investment"
            value={fmtInr(totals.invested)}
          />
          <ScorecardCell
            label="Current value"
            value={fmtInr(totals.current)}
          />
          <ScorecardCell
            label="Total P&L"
            value={fmtSigned(totals.pnl)}
            sub={fmtPct(totals.pnlPct)}
            valueClass={pnlClass(totals.pnl)}
          />
        </div>
      )}

      {/* Table */}
      <table
        className="w-full text-sm"
        data-testid="holdings-table"
      >
        <thead
          className="text-xs uppercase text-slate-500 border-b
            border-slate-200 dark:border-slate-700"
        >
          <tr>
            <th className="px-2 py-2 text-left">Ticker</th>
            <th className="px-2 py-2 text-right">Qty</th>
            <th className="px-2 py-2 text-right">Avg cost</th>
            <th className="px-2 py-2 text-right">LTP</th>
            <th className="px-2 py-2 text-right">Invested</th>
            <th className="px-2 py-2 text-right">Cur. val</th>
            <th className="px-2 py-2 text-right">P&amp;L</th>
            <th className="px-2 py-2 text-right">P&amp;L%</th>
            <th className="px-2 py-2 text-right">Days</th>
            <th className="px-2 py-2 text-left">Strategy</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const qty = r.quantity;
            const avg = Number(r.average_price);
            const ltp = Number(r.last_price);
            const rowInvested = qty * (Number.isFinite(avg) ? avg : 0);
            const rowCurrent = qty * (Number.isFinite(ltp) ? ltp : 0);
            const rowPnl = Number(r.pnl_inr);
            return (
              <tr
                key={r.tradingsymbol}
                className="border-b border-slate-100 dark:border-slate-800"
                data-testid={`holdings-row-${r.tradingsymbol}`}
              >
                <td className="px-2 py-2 font-medium">
                  <span className="inline-flex items-center gap-1.5">
                    {r.tradingsymbol}
                    {r.t1_pending && (
                      <span
                        title="Shares from yesterday's CNC BUY are settling today (T+1). Sellable via regular CNC sell order."
                        className="inline-flex items-center rounded
                          bg-amber-100 px-1.5 py-0.5 text-[10px]
                          font-semibold uppercase tracking-wide
                          text-amber-800 dark:bg-amber-900/40
                          dark:text-amber-200"
                        data-testid={`holdings-t1-chip-${r.tradingsymbol}`}
                      >
                        T+1
                      </span>
                    )}
                  </span>
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {r.quantity}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {fmtInr(r.average_price)}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {fmtInr(r.last_price)}
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-slate-600 dark:text-slate-400">
                  {qty > 0 ? fmtInr(rowInvested) : "—"}
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-slate-600 dark:text-slate-400">
                  {qty > 0 ? fmtInr(rowCurrent) : "—"}
                </td>
                <td
                  className={`px-2 py-2 text-right tabular-nums font-medium
                    ${pnlClass(rowPnl)}`}
                >
                  {fmtInr(r.pnl_inr)}
                </td>
                <td
                  className={`px-2 py-2 text-right tabular-nums
                    ${pnlClass(Number(r.pnl_pct))}`}
                >
                  {fmtPct(r.pnl_pct)}
                </td>
                <td className="px-2 py-2 text-right text-slate-500">
                  {r.days_held != null ? `${r.days_held}d` : "—"}
                </td>
                <td className="px-2 py-2 text-slate-600 dark:text-slate-400">
                  {r.strategy_name ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>

        {/* Total row */}
        {totals && (
          <tfoot>
            <tr
              className="border-t-2 border-slate-300 dark:border-slate-600
                bg-slate-50 dark:bg-slate-800/50 font-semibold text-sm"
              data-testid="holdings-total-row"
            >
              <td className="px-2 py-2 text-slate-700 dark:text-slate-300">
                Total
              </td>
              <td />
              <td />
              <td />
              <td className="px-2 py-2 text-right tabular-nums text-slate-700 dark:text-slate-300">
                {fmtInr(totals.invested)}
              </td>
              <td className="px-2 py-2 text-right tabular-nums text-slate-700 dark:text-slate-300">
                {fmtInr(totals.current)}
              </td>
              <td
                className={`px-2 py-2 text-right tabular-nums
                  ${pnlClass(totals.pnl)}`}
              >
                {fmtSigned(totals.pnl)}
              </td>
              <td
                className={`px-2 py-2 text-right tabular-nums
                  ${pnlClass(totals.pnlPct)}`}
              >
                {fmtPct(totals.pnlPct)}
              </td>
              <td />
              <td />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
