"use client";

import { useMemo } from "react";

import { ColumnSelector, type ColumnSpec } from "@/components/insights/ColumnSelector";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import type { TradeRow } from "@/hooks/useBacktestRuns";
import { downloadCsv, type CsvColumn } from "@/lib/downloadCsv";
import { useColumnSelection } from "@/lib/useColumnSelection";

const ALL_COLS: ColumnSpec[] = [
  { key: "ticker", label: "Ticker", category: "Identity" },
  { key: "qty", label: "Qty", category: "Trade" },
  { key: "avg_price", label: "Avg ₹", category: "Trade" },
  { key: "fill_price", label: "Fill ₹", category: "Trade" },
  { key: "opened_at", label: "Opened", category: "Trade" },
  { key: "closed_at", label: "Closed", category: "Trade" },
  { key: "holding_days", label: "Days", category: "Trade" },
  {
    key: "realised_pnl_inr",
    label: "PnL ₹",
    category: "Performance",
  },
  {
    key: "return_pct",
    label: "Return %",
    category: "Performance",
  },
];

const DEFAULT_COLS = [
  "ticker",
  "qty",
  "avg_price",
  "fill_price",
  "opened_at",
  "closed_at",
  "holding_days",
  "realised_pnl_inr",
  "return_pct",
];

const VALID_KEYS = ALL_COLS.map((c) => c.key);

interface Props {
  rows: TradeRow[];
}

export function BacktestTradeTable({ rows }: Props) {
  const [selected, setSelected, reset] = useColumnSelection(
    "algo:backtest:trade-cols",
    DEFAULT_COLS,
    VALID_KEYS,
  );
  const visibleCols = useMemo(
    () => ALL_COLS.filter((c) => selected.includes(c.key)),
    [selected],
  );

  const handleDownload = () => {
    if (rows.length === 0) return;
    const csvCols: CsvColumn<TradeRow>[] = visibleCols.map((c) => ({
      key: c.key as keyof TradeRow & string,
      header: c.label,
    }));
    downloadCsv(rows, csvCols, "backtest-trades");
  };

  if (rows.length === 0) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="backtest-trade-table-empty"
      >
        No closed trades yet — run a strategy that exits positions.
      </div>
    );
  }

  return (
    <div
      className="space-y-2"
      data-testid="backtest-trade-table"
    >
      <div className="flex items-center justify-between">
        <ColumnSelector
          catalog={ALL_COLS}
          selected={selected}
          onChange={setSelected}
          onReset={reset}
          lockedKeys={["ticker"]}
        />
        <DownloadCsvButton
          onClick={handleDownload}
          disabled={rows.length === 0}
          aria-label="Download CSV"
          title="Download CSV"
        />
      </div>
      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 dark:bg-slate-800">
            <tr>
              {visibleCols.map((c) => (
                <th
                  key={c.key}
                  className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300"
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${r.ticker}-${r.closed_at}-${i}`}
                className="border-t border-slate-200 dark:border-slate-700"
              >
                {visibleCols.map((c) => (
                  <td
                    key={c.key}
                    className="px-3 py-1.5 text-slate-800 dark:text-slate-200"
                  >
                    {String(r[c.key as keyof TradeRow])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
