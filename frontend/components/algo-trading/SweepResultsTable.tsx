"use client";

import type { SweepResult } from "@/lib/types/algoSweep";
import { useState } from "react";
import { SweepPromoteModal } from "./SweepPromoteModal";

interface Props { run: SweepResult; }

export function SweepResultsTable({ run }: Props) {
  const [promoteOpen, setPromoteOpen] = useState(false);

  const sorted = [...run.variants].sort((a, b) => {
    const da = Number(b.sharpe) - Number(a.sharpe);
    if (da !== 0) return da;
    return a.variant_index - b.variant_index;
  });

  // Tie-aware rank: same Sharpe → same rank number.
  const rankBySharpe = new Map<number, number>();
  let rank = 0;
  let prev: number | null = null;
  for (const [i, v] of sorted.entries()) {
    const s = Number(v.sharpe);
    if (prev === null || s !== prev) rank = i + 1;
    rankBySharpe.set(v.variant_index, rank);
    prev = s;
  }

  return (
    <div
      className="rounded-md border"
      data-testid="sweep-results-table"
    >
      <table className="w-full text-xs">
        <thead className="bg-slate-50 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-1.5 text-left">Rank</th>
            <th className="px-3 py-1.5 text-left">Value</th>
            <th className="px-3 py-1.5 text-right">Trades</th>
            <th className="px-3 py-1.5 text-right">Win %</th>
            <th className="px-3 py-1.5 text-right">PnL %</th>
            <th className="px-3 py-1.5 text-right">Max DD %</th>
            <th className="px-3 py-1.5 text-right">Sharpe</th>
            <th className="px-3 py-1.5 text-right">DSR</th>
            <th className="px-3 py-1.5 text-left">Action</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((v) => {
            const r = rankBySharpe.get(v.variant_index)!;
            return (
              <tr
                key={v.variant_index}
                data-testid={
                  `sweep-results-row-${v.variant_index}`
                }
                className="border-t"
              >
                <td className="px-3 py-1.5">
                  {r === 1 ? "🏆 " : ""}{r}
                </td>
                <td className="px-3 py-1.5">
                  {String(v.swept_value)}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.n_trades}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.avg_win_rate_pct}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.avg_pnl_pct}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.avg_max_drawdown_pct}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.sharpe}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {v.dsr}
                </td>
                <td className="px-3 py-1.5">
                  <a
                    href={
                      `/algo-trading/strategies?tab=backtest`
                      + `&walkforward_id=`
                      + v.walkforward_run_id
                    }
                    className="text-indigo-600 underline"
                  >
                    View →
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {run.winner_variant_index !== null && (
        <div className="p-3 border-t">
          <button
            type="button"
            onClick={() => setPromoteOpen(true)}
            className="rounded bg-emerald-600 text-white px-3 py-1.5 text-sm"
            data-testid="sweep-promote-winner-button"
          >
            Save winner as new strategy
          </button>
        </div>
      )}
      {promoteOpen && (
        <SweepPromoteModal
          run={run}
          onClose={() => setPromoteOpen(false)}
        />
      )}
    </div>
  );
}
