"use client";

import { useLiveHoldings } from "@/hooks/useLiveHoldings";

function fmtInr(v: string | null | undefined): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `₹${n.toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  })}`;
}

function fmtPct(v: string | null | undefined): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

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
    <table
      className="w-full text-sm"
      data-testid="holdings-table"
    >
      <thead className="text-xs uppercase text-slate-500 border-b border-slate-200 dark:border-slate-700">
        <tr>
          <th className="px-2 py-2 text-left">Ticker</th>
          <th className="px-2 py-2 text-right">Qty</th>
          <th className="px-2 py-2 text-right">Avg</th>
          <th className="px-2 py-2 text-right">LTP</th>
          <th className="px-2 py-2 text-right">P&amp;L</th>
          <th className="px-2 py-2 text-right">P&amp;L%</th>
          <th className="px-2 py-2 text-right">Days</th>
          <th className="px-2 py-2 text-left">Strategy</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
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
            <td className="px-2 py-2 text-right">{r.quantity}</td>
            <td className="px-2 py-2 text-right">
              {fmtInr(r.average_price)}
            </td>
            <td className="px-2 py-2 text-right">
              {fmtInr(r.last_price)}
            </td>
            <td className="px-2 py-2 text-right">
              {fmtInr(r.pnl_inr)}
            </td>
            <td className="px-2 py-2 text-right">
              {fmtPct(r.pnl_pct)}
            </td>
            <td className="px-2 py-2 text-right">
              {r.days_held != null ? `${r.days_held}d` : "—"}
            </td>
            <td className="px-2 py-2">{r.strategy_name ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
