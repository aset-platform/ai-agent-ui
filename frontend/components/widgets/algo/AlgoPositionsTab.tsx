"use client";

import { useMemo } from "react";
import Link from "next/link";

import { useAlgoPositions } from "@/hooks/useAlgoPositions";
import { AlgoPositionRow } from "./AlgoPositionRow";

interface Props {
  onSelectTicker?: (ticker: string) => void;
}

function fmtInr(n: number): string {
  return `₹${Math.abs(n).toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  })}`;
}

function pnlClass(v: number): string {
  return v >= 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}

export function AlgoPositionsTab({ onSelectTicker }: Props) {
  const { positions, isLoading, error } = useAlgoPositions();

  const totals = useMemo(() => {
    if (positions.length === 0) return null;
    let invested = 0;
    let current = 0;
    let pnl = 0;
    for (const p of positions) {
      const qty = p.quantity;
      const avg = Number(p.avg_price);
      const ltp = Number(p.last_price);
      const pnlRow = Number(p.pnl_inr);
      if (qty > 0) {
        invested += qty * (Number.isFinite(avg) ? avg : 0);
        current += qty * (Number.isFinite(ltp) ? ltp : 0);
      }
      if (Number.isFinite(pnlRow)) pnl += pnlRow;
    }
    const pnlPct = invested > 0 ? (pnl / invested) * 100 : 0;
    return { invested, current, pnl, pnlPct };
  }, [positions]);

  if (isLoading) {
    return (
      <div
        className="px-5 py-10 text-center"
        data-testid="dashboard-algo-positions-loading"
      >
        <div className="animate-spin h-6 w-6 border-2 border-indigo-500 border-t-transparent rounded-full mx-auto" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="m-5 rounded-md border border-rose-200 bg-rose-50 dark:bg-rose-950/30 p-3 text-xs text-rose-700"
        data-testid="dashboard-algo-positions-error"
      >
        Algo positions unavailable
      </div>
    );
  }

  if (positions.length === 0) {
    return (
      <div
        className="m-5 rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/30 p-4 text-xs space-y-2"
        data-testid="dashboard-algo-positions-empty"
      >
        <p className="font-medium text-amber-900 dark:text-amber-200">
          No algo positions open.
        </p>
        <p className="text-amber-800 dark:text-amber-300">
          Live algo trading places intraday + overnight positions that
          show up here.
        </p>
        <Link
          href="/algo-trading/strategies?tab=live"
          className="inline-block rounded bg-indigo-600 text-white px-3 py-1.5 text-xs"
          data-testid="dashboard-algo-positions-cta"
        >
          Set up a live strategy →
        </Link>
      </div>
    );
  }

  return (
    <div data-testid="dashboard-algo-positions-table">
      {/* Compact scorecard */}
      {totals && (
        <div
          className="mx-3 mt-2 mb-3 grid grid-cols-3 gap-2 rounded-md
            border border-slate-200 bg-slate-50 px-3 py-2
            dark:border-slate-700 dark:bg-slate-800/50"
          data-testid="dashboard-algo-scorecard"
        >
          <div className="flex flex-col">
            <span className="text-[10px] text-slate-500 dark:text-slate-400">
              Invested
            </span>
            <span className="text-xs font-semibold tabular-nums text-slate-800 dark:text-slate-200">
              {fmtInr(totals.invested)}
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] text-slate-500 dark:text-slate-400">
              Current
            </span>
            <span className="text-xs font-semibold tabular-nums text-slate-800 dark:text-slate-200">
              {fmtInr(totals.current)}
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] text-slate-500 dark:text-slate-400">
              P&amp;L
            </span>
            <span
              className={`text-xs font-semibold tabular-nums
                ${pnlClass(totals.pnl)}`}
            >
              {totals.pnl >= 0 ? "+" : "−"}
              {fmtInr(totals.pnl)}
              {" "}
              <span className="font-normal">
                ({totals.pnl >= 0 ? "+" : ""}
                {totals.pnlPct.toFixed(2)}%)
              </span>
            </span>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 dark:bg-gray-800 border-b border-gray-100 dark:border-gray-800">
            <tr>
              <th className="px-3 py-2 text-left font-semibold text-gray-500">
                Symbol
              </th>
              <th className="px-3 py-2 text-left font-semibold text-gray-500">
                Qty
              </th>
              <th className="px-3 py-2 text-right font-semibold text-gray-500">
                Avg
              </th>
              <th className="px-3 py-2 text-right font-semibold text-gray-500">
                LTP
              </th>
              <th className="px-3 py-2 text-right font-semibold text-gray-500">
                PnL %
              </th>
              <th className="px-3 py-2 text-left font-semibold text-gray-500">
                Strategy
              </th>
              <th className="px-3 py-2 text-right font-semibold text-gray-500">
                RSI2
              </th>
              <th className="px-3 py-2 text-right font-semibold text-gray-500">
                Days
              </th>
            </tr>
          </thead>
          <tbody>
            {positions.map((row) => (
              <AlgoPositionRow
                key={`${row.tradingsymbol}-${row.product}`}
                row={row}
                onSelectTicker={onSelectTicker}
              />
            ))}
          </tbody>

          {/* Total row */}
          {totals && (
            <tfoot>
              <tr
                className="border-t border-gray-200 dark:border-gray-700
                  bg-gray-50 dark:bg-gray-800 font-semibold"
                data-testid="dashboard-algo-total-row"
              >
                <td
                  colSpan={4}
                  className="px-3 py-1.5 text-xs text-gray-500"
                >
                  Total ({positions.length})
                </td>
                <td
                  className={`px-3 py-1.5 text-xs text-right tabular-nums
                    ${pnlClass(totals.pnl)}`}
                >
                  {totals.pnl >= 0 ? "+" : ""}
                  {totals.pnlPct.toFixed(2)}%
                </td>
                <td colSpan={3} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
