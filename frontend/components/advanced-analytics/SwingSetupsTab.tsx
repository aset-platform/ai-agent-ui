"use client";

/**
 * Top-level Swing Setups tab — Bull / Sideways / Bearish
 * pills + collapsible methodology panel + a per-regime
 * ranked table.
 *
 * Table columns are regime-specific (see ``COLS`` below);
 * full ColumnSelector is intentionally deferred to a
 * follow-up so Phase A ships a focused list view.
 */

import { useState } from "react";

import { StockAnalysisLink } from "./StockAnalysisLink";
import { SwingMethodologyPanel } from "./SwingMethodologyPanel";
import { SwingRegimePills } from "./SwingRegimePills";
import { useSwingSetups } from "@/hooks/useSwingSetups";
import type { AdvancedRow } from "@/lib/types/advancedAnalytics";
import type { SwingRegime } from "@/lib/types/swingSetups";

interface Col {
  key: keyof AdvancedRow;
  label: string;
  fmt?: (
    v: AdvancedRow[keyof AdvancedRow],
  ) => string;
}

const fmtNum = (digits: number) => (
  v: AdvancedRow[keyof AdvancedRow],
) => (typeof v === "number" ? v.toFixed(digits) : "—");

const fmtPct = (
  v: AdvancedRow[keyof AdvancedRow],
) => (typeof v === "number" ? `${v.toFixed(1)}%` : "—");

const fmtStr = (
  v: AdvancedRow[keyof AdvancedRow],
) => (typeof v === "string" ? v : "—");

const COLS: Record<SwingRegime, Col[]> = {
  bull: [
    { key: "ticker", label: "Ticker", fmt: fmtStr },
    { key: "sector", label: "Sector", fmt: fmtStr },
    { key: "today_ltp", label: "LTP", fmt: fmtNum(2) },
    { key: "today_x_vol", label: "Vol×", fmt: fmtNum(2) },
    { key: "current_dpc", label: "Del%", fmt: fmtPct },
    { key: "x_dv_20d", label: "DelV20×", fmt: fmtNum(2) },
    { key: "rsi", label: "RSI", fmt: fmtNum(1) },
    { key: "rec_category", label: "Rec", fmt: fmtStr },
    {
      key: "rec_expected_return_pct",
      label: "Rec %",
      fmt: fmtPct,
    },
    { key: "pscore", label: "P-Score", fmt: fmtNum(0) },
  ],
  sideways: [
    { key: "ticker", label: "Ticker", fmt: fmtStr },
    { key: "sector", label: "Sector", fmt: fmtStr },
    { key: "today_ltp", label: "LTP", fmt: fmtNum(2) },
    { key: "sma_50", label: "SMA-50", fmt: fmtNum(2) },
    { key: "rsi", label: "RSI", fmt: fmtNum(1) },
    { key: "today_x_vol", label: "Vol×", fmt: fmtNum(2) },
    {
      key: "rolling_low_20d_prev",
      label: "20d Low",
      fmt: fmtNum(2),
    },
    {
      key: "rolling_high_20d_prev",
      label: "20d High",
      fmt: fmtNum(2),
    },
    { key: "pscore", label: "P-Score", fmt: fmtNum(0) },
  ],
  bearish: [
    { key: "ticker", label: "Ticker", fmt: fmtStr },
    { key: "sector", label: "Sector", fmt: fmtStr },
    { key: "today_ltp", label: "LTP", fmt: fmtNum(2) },
    { key: "today_low", label: "Low", fmt: fmtNum(2) },
    {
      key: "death_cross_days_ago",
      label: "Death×d",
      fmt: fmtNum(0),
    },
    { key: "rsi", label: "RSI", fmt: fmtNum(1) },
    { key: "rsi_max_10d", label: "RSI Max 10d", fmt: fmtNum(1) },
    {
      key: "rolling_low_20d_prev",
      label: "20d Low",
      fmt: fmtNum(2),
    },
  ],
};

export function SwingSetupsTab() {
  const [regime, setRegime] = useState<SwingRegime>("bull");
  const { data, error, isLoading } = useSwingSetups({
    regime,
    market: "all",
    page: 1,
    pageSize: 25,
    sortKey: null,
    sortDir: "desc",
  });

  const cols = COLS[regime];

  return (
    <div className="space-y-4" data-testid="swing-setups-tab">
      <div className="flex items-center justify-between">
        <SwingRegimePills
          value={regime}
          onChange={setRegime}
        />
        {data?.as_of && (
          <span className="text-xs text-slate-500">
            As of {data.as_of}
          </span>
        )}
      </div>

      {data?.methodology && (
        <SwingMethodologyPanel
          methodology={data.methodology}
          recGateApplied={data.rec_gate_applied}
          notes={data.notes}
        />
      )}

      {error && (
        <div
          data-testid="swing-error"
          className="rounded-md border border-red-300 bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-300"
        >
          Failed to load swing setups: {String(error)}
        </div>
      )}

      {isLoading && !data && (
        <div
          data-testid="swing-loading"
          className="py-12 text-center text-slate-500"
        >
          Loading swing setups…
        </div>
      )}

      {data && data.rows.length === 0 && (
        <div
          data-testid="swing-empty"
          className="py-12 text-center text-slate-500"
        >
          No {regime}-swing setups match today.
        </div>
      )}

      {data && data.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table
            data-testid="swing-table"
            className="min-w-full text-sm"
          >
            <thead>
              <tr className="border-b border-slate-300 dark:border-slate-700 text-left">
                {cols.map((c) => (
                  <th
                    key={c.key as string}
                    className="px-3 py-2 font-medium text-slate-700 dark:text-slate-200"
                  >
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr
                  key={row.ticker}
                  data-testid={`swing-row-${row.ticker}`}
                  className="border-b border-slate-200 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-900/40"
                >
                  {cols.map((c) => (
                    <td
                      key={c.key as string}
                      className="px-3 py-2 text-slate-800 dark:text-slate-100"
                    >
                      {c.key === "ticker" ? (
                        <span className="inline-flex items-center gap-1.5">
                          <StockAnalysisLink
                            ticker={row.ticker}
                            testId={`swing-chart-link-${row.ticker}`}
                          />
                          <span className="font-mono">{String(row.ticker)}</span>
                        </span>
                      ) : (
                        c.fmt
                          ? c.fmt(row[c.key])
                          : String(row[c.key] ?? "—")
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
