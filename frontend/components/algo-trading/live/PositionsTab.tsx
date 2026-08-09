"use client";

import { useLivePositions } from "@/hooks/useLivePositions";

function rsi2Display(v: string | null | undefined): string {
  const n = Number(v ?? "");
  if (!Number.isFinite(n) || v == null) return "—";
  return n.toFixed(1);
}

function rsi2Class(v: string | null | undefined): string {
  const n = Number(v ?? "");
  if (!Number.isFinite(n) || v == null) return "text-slate-400";
  if (n <= 10) return "text-emerald-600";
  if (n >= 90) return "text-rose-600";
  return "";
}

function fmt(
  v: string | null | undefined,
  kind: "inr" | "pct" | "qty",
): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (kind === "inr") {
    return `₹${n.toLocaleString("en-IN", {
      maximumFractionDigits: 2,
    })}`;
  }
  if (kind === "pct") {
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  }
  return String(n);
}

function totalPnl(rows: { pnl_inr: string }[]): number {
  return rows.reduce((sum, r) => {
    const n = Number(r.pnl_inr);
    return Number.isFinite(n) ? sum + n : sum;
  }, 0);
}

/**
 * PositionsTab — intraday MIS positions table with strategy join.
 *
 * Data source: {@link useLivePositions} (`/algo/live/positions`),
 * which already merges Kite's net positions with the
 * algo_live_orders ledger so each row carries `strategy_name`,
 * `entry_ts_utc`, `entry_reason` when the position was opened by
 * an algo. Manual / unattributed positions display "—" in those
 * columns.
 */
export function PositionsTab() {
  const { rows, loading, error } = useLivePositions();
  if (loading) {
    return (
      <p
        className="text-sm text-slate-500"
        data-testid="positions-loading"
      >
        Loading…
      </p>
    );
  }
  if (error) {
    return (
      <p
        className="text-sm text-rose-700"
        data-testid="positions-error"
      >
        Could not load positions: {String(error)}
      </p>
    );
  }
  if (!rows || rows.length === 0) {
    return (
      <p
        className="text-sm text-slate-500"
        data-testid="positions-empty"
      >
        No open positions.
      </p>
    );
  }
  return (
    <table
      className="w-full text-sm"
      data-testid="positions-table"
    >
      <thead className="text-xs uppercase text-slate-500 border-b border-slate-200 dark:border-slate-700">
        <tr>
          <th className="px-2 py-2 text-left">Ticker</th>
          <th className="px-2 py-2 text-right">Qty</th>
          <th className="px-2 py-2 text-right">Avg</th>
          <th className="px-2 py-2 text-right">LTP</th>
          <th className="px-2 py-2 text-right">P&amp;L</th>
          <th className="px-2 py-2 text-right">P&amp;L%</th>
          <th className="px-2 py-2 text-left">Strategy</th>
          <th className="px-2 py-2 text-left">Entry</th>
          <th className="px-2 py-2 text-right">RSI2</th>
          <th className="px-2 py-2 text-left">Reason</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={`${r.tradingsymbol}-${r.product}`}
            className="border-b border-slate-100 dark:border-slate-800"
          >
            <td className="px-2 py-2 font-medium">
              {r.tradingsymbol}
            </td>
            <td className="px-2 py-2 text-right">{r.quantity}</td>
            <td className="px-2 py-2 text-right">
              {fmt(r.average_price, "inr")}
            </td>
            <td className="px-2 py-2 text-right">
              {fmt(r.last_price, "inr")}
            </td>
            <td className="px-2 py-2 text-right">
              {fmt(r.pnl_inr, "inr")}
            </td>
            <td className="px-2 py-2 text-right">
              {fmt(r.pnl_pct, "pct")}
            </td>
            <td className="px-2 py-2">{r.strategy_name ?? "—"}</td>
            <td className="px-2 py-2 text-xs text-slate-500">
              {r.entry_ts_utc
                ? new Date(r.entry_ts_utc).toLocaleTimeString(
                    "en-IN",
                  )
                : "—"}
            </td>
            <td
              className={`px-2 py-2 text-right tabular-nums ${rsi2Class(r.rsi_2)}`}
            >
              {rsi2Display(r.rsi_2)}
            </td>
            <td className="px-2 py-2 text-xs">
              {r.entry_reason ?? "—"}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="border-t border-slate-200 dark:border-slate-700 font-medium">
          <td className="px-2 py-2 text-right" colSpan={4}>
            Total P&amp;L
          </td>
          <td
            className={`px-2 py-2 text-right tabular-nums ${
              totalPnl(rows) >= 0
                ? "text-emerald-600"
                : "text-rose-600"
            }`}
            data-testid="positions-total-pnl"
          >
            {fmt(String(totalPnl(rows)), "inr")}
          </td>
          <td colSpan={5} />
        </tr>
      </tfoot>
    </table>
  );
}
