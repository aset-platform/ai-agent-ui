"use client";

import { useLivePositions } from "@/hooks/useLivePositions";

function pct(v: string | null | undefined): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return "0.00%";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function price(v: string | null | undefined): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return "₹0.00";
  return `₹${n.toFixed(2)}`;
}

function rsi2Badge(v: string | null | undefined): {
  text: string;
  cls: string;
} {
  const n = Number(v ?? "");
  if (!Number.isFinite(n) || v == null)
    return { text: "—", cls: "text-slate-400" };
  const cls =
    n <= 10
      ? "text-emerald-600"
      : n >= 90
        ? "text-rose-600"
        : "text-slate-500";
  return { text: n.toFixed(1), cls };
}

/**
 * OpenPositionsWidget — Zone-A compact panel for the LiveDashboard
 * grid. Mirrors {@link PositionsTab} (same hook) but trims to the
 * top-5 rows and presents inline rather than as a table.
 */
export function OpenPositionsWidget() {
  const { rows, loading } = useLivePositions();
  const visible = (rows ?? []).slice(0, 5);
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
      data-testid="open-positions-widget"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Open Positions
        </h3>
        <span className="text-xs text-slate-400">
          {rows?.length ?? 0}
        </span>
      </div>
      {loading && (
        <p className="mt-2 text-xs text-slate-400">Loading…</p>
      )}
      {!loading && visible.length === 0 && (
        <p className="mt-2 text-xs text-slate-400">
          No open positions.
        </p>
      )}
      {!loading && visible.length > 0 && (
        <div className="mt-2">
          {/* Header */}
          <div className="grid grid-cols-[1fr_2rem_5.5rem_4.5rem_3rem] text-[10px] font-semibold uppercase tracking-wide text-slate-400 pb-1 border-b border-slate-100 dark:border-slate-800">
            <span>Symbol</span>
            <span className="text-right">Qty</span>
            <span className="text-right">LTP</span>
            <span className="text-right">P&amp;L</span>
            <span className="text-right">RSI(2)</span>
          </div>
          {/* Rows */}
          <ul className="mt-1 space-y-0.5">
            {visible.map((r) => {
              const n = Number(r.pnl_pct);
              const positive = Number.isFinite(n) ? n >= 0 : true;
              const rsi = rsi2Badge(r.rsi_2);
              return (
                <li
                  key={`${r.tradingsymbol}-${r.product}`}
                  className="grid grid-cols-[1fr_2rem_5.5rem_4.5rem_3rem] items-center text-xs tabular-nums py-0.5"
                >
                  <span className="font-medium text-slate-800 dark:text-slate-200 truncate">
                    {r.tradingsymbol}
                  </span>
                  <span className="text-right text-slate-600 dark:text-slate-400">
                    {r.quantity}
                  </span>
                  <span className="text-right text-slate-600 dark:text-slate-400">
                    {price(r.last_price)}
                  </span>
                  <span
                    className={`text-right ${positive ? "text-emerald-600" : "text-rose-600"}`}
                  >
                    {pct(r.pnl_pct)}
                  </span>
                  <span
                    className={`text-right font-medium ${rsi.cls}`}
                    title="RSI(2)"
                  >
                    {rsi.text}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {rows && rows.length > 5 && (
        <p className="mt-2 text-xs text-slate-400">
          +{rows.length - 5} more — see Positions tab.
        </p>
      )}
    </div>
  );
}
