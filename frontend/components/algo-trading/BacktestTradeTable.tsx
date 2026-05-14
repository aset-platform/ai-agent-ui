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
  { key: "exit_reason", label: "Exit", category: "Trade" },
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
  "exit_reason",
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
                    {renderCell(r, c.key)}
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

function renderCell(r: TradeRow, key: string): React.ReactNode {
  if (key === "exit_reason") {
    return (
      <ExitReasonBadge reason={(r.exit_reason as string) || "signal"} />
    );
  }
  if (key === "opened_at") {
    return formatTradeTime(r.opened_at, r.opened_at_ts_ns);
  }
  if (key === "closed_at") {
    return formatTradeTime(r.closed_at, r.closed_at_ts_ns);
  }
  // Money / price columns — cap at 2 decimals with Indian
  // thousands separators so the table doesn't bleed across the
  // viewport with raw Decimal precision from the backend.
  if (
    key === "avg_price" ||
    key === "fill_price" ||
    key === "realised_pnl_inr"
  ) {
    return formatInr(r[key as keyof TradeRow] as string | number);
  }
  if (key === "return_pct") {
    return formatPct(r.return_pct);
  }
  return String(r[key as keyof TradeRow]);
}

function formatInr(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return String(v);
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

function formatPct(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return String(v);
  return `${n.toFixed(2)}%`;
}

// Format a fill date/time. When the intraday ts is present, render
// "YYYY-MM-DD HH:mm IST" so the user can spot time-sensitive
// trades (e.g. an MIS scalp that opens at 10:15 and closes at
// 15:10). Daily-cadence trades fall back to the bare date.
function formatTradeTime(
  fallbackDate: string,
  tsNs: number | null | undefined,
): string {
  if (tsNs == null) return fallbackDate;
  const ms = tsNs / 1_000_000;
  const d = new Date(ms);
  const fmt = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  // Output like "14/05/2026, 15:10". Normalise to ISO-ish:
  const parts = fmt.formatToParts(d);
  const get = (t: string) =>
    parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get(
    "hour",
  )}:${get("minute")} IST`;
}

function ExitReasonBadge({ reason }: { reason: string }) {
  const styles: Record<string, string> = {
    signal:
      "bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600",
    stop_loss:
      "bg-rose-100 text-rose-700 border-rose-300 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-700",
    mis_square_off:
      "bg-sky-100 text-sky-700 border-sky-300 dark:bg-sky-900/30 dark:text-sky-300 dark:border-sky-700",
    period_end_mtm:
      "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700",
  };
  const cls = styles[reason] ?? styles.signal;
  const labels: Record<string, string> = {
    signal: "Signal",
    stop_loss: "Stop-loss",
    mis_square_off: "MIS square-off",
    period_end_mtm: "Period end (MTM)",
  };
  const titles: Record<string, string> = {
    signal: "Closed by the strategy's exit rule.",
    stop_loss: "Per-trade stop-loss tripped.",
    mis_square_off:
      "Auto-closed at the end of the trading day (MIS contract).",
    period_end_mtm:
      "Open at backtest period end; force-closed at the last bar's close to reconcile total PnL with the trade list.",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${cls}`}
      title={titles[reason] ?? ""}
    >
      {labels[reason] ?? reason}
    </span>
  );
}
