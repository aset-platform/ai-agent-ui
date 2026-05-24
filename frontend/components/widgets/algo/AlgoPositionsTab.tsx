"use client";

import Link from "next/link";
import { useAlgoPositions } from "@/hooks/useAlgoPositions";
import { AlgoPositionRow } from "./AlgoPositionRow";

interface Props {
  onSelectTicker?: (ticker: string) => void;
}

export function AlgoPositionsTab({ onSelectTicker }: Props) {
  const { positions, isLoading, error } = useAlgoPositions();

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
    <div
      className="overflow-x-auto"
      data-testid="dashboard-algo-positions-table"
    >
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
      </table>
    </div>
  );
}
