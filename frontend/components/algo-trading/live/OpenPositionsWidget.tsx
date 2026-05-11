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
      <ul className="mt-2 space-y-1 text-sm">
        {visible.map((r) => {
          const n = Number(r.pnl_pct);
          const positive = Number.isFinite(n) ? n >= 0 : true;
          return (
            <li
              key={`${r.tradingsymbol}-${r.product}`}
              className="flex justify-between"
            >
              <span className="font-medium">{r.tradingsymbol}</span>
              <span className="tabular-nums text-slate-600 dark:text-slate-400">
                {r.quantity} · {price(r.last_price)} ·
                <span
                  className={
                    positive
                      ? "text-emerald-600 ml-1"
                      : "text-rose-600 ml-1"
                  }
                >
                  {pct(r.pnl_pct)}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      {(rows?.length ?? 0) > 5 && (
        <p className="mt-2 text-xs text-slate-400">
          +{(rows!.length - 5)} more — see Positions tab.
        </p>
      )}
    </div>
  );
}
