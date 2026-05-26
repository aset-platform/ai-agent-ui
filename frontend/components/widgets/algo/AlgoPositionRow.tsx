"use client";

import type { AlgoPositionView } from "@/lib/types/algoPortfolio";

interface Props {
  row: AlgoPositionView;
  onSelectTicker?: (ticker: string) => void;
}

function inr(s: string): string {
  const n = Number(s);
  if (!Number.isFinite(n)) return "₹0";
  return `₹${n.toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  })}`;
}

function pctStr(s: string): string {
  const n = Number(s);
  if (!Number.isFinite(n)) return "0.00%";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

export function AlgoPositionRow({ row, onSelectTicker }: Props) {
  const pnl = Number(row.pnl_pct);
  const positive = Number.isFinite(pnl) && pnl >= 0;
  return (
    <tr
      onClick={() => onSelectTicker?.(row.internal_ticker)}
      className="border-t border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
      data-testid={`dashboard-algo-row-${row.tradingsymbol}`}
    >
      <td className="px-3 py-2 text-xs font-medium">
        <span>{row.tradingsymbol}</span>
        {row.source === "paper" && (
          <span
            data-testid={
              `dashboard-algo-row-${row.tradingsymbol}-paper-badge`
            }
            className="ml-1 inline-block rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 text-[10px] font-semibold uppercase px-1 py-0.5 align-middle"
            title="Position derived from paper-mode fills (not Kite)"
          >
            PAPER
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-xs tabular-nums">
        {row.t1_pending ? (
          <span>
            0+
            <em className="not-italic text-amber-600">
              {row.quantity}
            </em>{" "}
            <span className="text-amber-600">T+1</span>
          </span>
        ) : (
          row.quantity
        )}
      </td>
      <td className="px-3 py-2 text-xs tabular-nums text-right">
        {inr(row.avg_price)}
      </td>
      <td className="px-3 py-2 text-xs tabular-nums text-right">
        {inr(row.last_price)}
      </td>
      <td
        className={`px-3 py-2 text-xs tabular-nums text-right ${
          positive ? "text-emerald-600" : "text-rose-600"
        }`}
      >
        {pctStr(row.pnl_pct)}
      </td>
      <td
        className="px-3 py-2 text-xs truncate max-w-[14ch]"
        title={row.strategy_name}
      >
        {row.strategy_name}
      </td>
      <td className="px-3 py-2 text-xs text-right text-gray-500">
        {row.days_held}
      </td>
    </tr>
  );
}
