"use client";

import { useMemo, useState } from "react";

import { TradeLogTable } from "./TradeLogTable";
import {
  filterStrategiesByMode,
  useStrategies,
  type StrategyMode,
} from "@/hooks/useStrategies";
import {
  useStrategyPerformance,
  type LookbackPreset,
  type PerformanceMode,
  type StrategyPerfRow,
} from "@/hooks/useStrategyPerformance";

const MODE_OPTIONS: { value: PerformanceMode; label: string }[] = [
  { value: "backtest", label: "Backtest" },
  { value: "walkforward", label: "Walk-forward" },
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
];

const LOOKBACK_OPTIONS: { value: LookbackPreset; label: string }[] = [
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
  { value: "all", label: "All" },
];

// Strategy-dropdown scoping per mode. Backtest/Walk-forward show
// every strategy (matches the existing convention that those two
// pickers never filter by promotion mode). Paper shows strategies
// currently in paper OR live (a strategy graduated to live still
// keeps its paper history meaningful). Live shows only strategies
// currently promoted to live, per the literal 1a spec.
const STRATEGY_FILTER_FOR_MODE: Record<
  PerformanceMode, StrategyMode[] | null
> = {
  backtest: null,
  walkforward: null,
  paper: ["paper", "live"],
  live: ["live"],
};

function fmtInr(v: number | null): string {
  if (v === null) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(v);
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v.toFixed(2)}%`;
}

export function PerformanceTab() {
  const [mode, setMode] = useState<PerformanceMode>("live");
  const [strategyId, setStrategyId] = useState<string>("all");
  const [lookback, setLookback] = useState<LookbackPreset>("30d");
  const [customRange, setCustomRange] = useState<{
    start: string; end: string;
  } | null>(null);

  const { strategies: allStrategies } = useStrategies();
  const scopedStrategies = useMemo(() => {
    const allowedModes = STRATEGY_FILTER_FOR_MODE[mode];
    return allowedModes
      ? filterStrategiesByMode(allStrategies, allowedModes)
      : allStrategies.filter((s) => s.archived_at == null);
  }, [allStrategies, mode]);

  const customRangeComplete = !!(customRange?.start && customRange?.end);

  const {
    strategies: perfRows,
    trades,
    loading,
    error,
  } = useStrategyPerformance({
    mode,
    strategyId: strategyId === "all" ? null : strategyId,
    lookback: customRangeComplete ? null : lookback,
    start: customRangeComplete ? customRange!.start : null,
    end: customRangeComplete ? customRange!.end : null,
  });

  const perTickerRows = useMemo(() => {
    const buckets = new Map<
      string, { ticker: string; trades: number; pnl: number; wins: number }
    >();
    for (const t of trades) {
      const b = buckets.get(t.ticker) ?? {
        ticker: t.ticker, trades: 0, pnl: 0, wins: 0,
      };
      b.trades += 1;
      b.pnl += Number(t.realised_pnl_inr);
      if (Number(t.realised_pnl_inr) > 0) b.wins += 1;
      buckets.set(t.ticker, b);
    }
    return Array.from(buckets.values()).sort((a, b) => a.pnl - b.pnl);
  }, [trades]);

  const handleModeChange = (m: PerformanceMode) => {
    setMode(m);
    setStrategyId("all");
  };

  return (
    <div className="space-y-4" data-testid="performance-tab">
      <div>
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Performance
        </h2>
        <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
          Trade-level win rate, biggest win/loss, and profit
          factor per strategy.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div
          className="inline-flex rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden"
          data-testid="performance-mode-pills"
        >
          {MODE_OPTIONS.map((m) => (
            <button
              key={m.value}
              type="button"
              data-testid={`performance-mode-${m.value}`}
              onClick={() => handleModeChange(m.value)}
              className={`px-3 py-1.5 text-xs font-medium ${
                mode === m.value
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-300"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>

        <select
          data-testid="performance-strategy-select"
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs text-slate-700 dark:text-slate-300"
        >
          <option value="all">All strategies</option>
          {scopedStrategies.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>

        <div
          className="inline-flex rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden"
          data-testid="performance-lookback-pills"
        >
          {LOOKBACK_OPTIONS.map((l) => (
            <button
              key={l.value}
              type="button"
              data-testid={`performance-lookback-${l.value}`}
              onClick={() => {
                setLookback(l.value);
                setCustomRange(null);
              }}
              className={`px-3 py-1.5 text-xs font-medium ${
                !customRange && lookback === l.value
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-300"
              }`}
            >
              {l.label}
            </button>
          ))}
          <button
            type="button"
            data-testid="performance-lookback-custom"
            onClick={() =>
              setCustomRange((c) => c ?? { start: "", end: "" })
            }
            className={`px-3 py-1.5 text-xs font-medium ${
              customRange
                ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-300"
            }`}
          >
            Custom
          </button>
        </div>

        {customRange && (
          <div className="flex items-center gap-2">
            <input
              type="date"
              data-testid="performance-range-start"
              value={customRange.start}
              onChange={(e) =>
                setCustomRange((c) => ({
                  start: e.target.value, end: c?.end ?? "",
                }))
              }
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs"
            />
            <span className="text-xs text-slate-500">to</span>
            <input
              type="date"
              data-testid="performance-range-end"
              value={customRange.end}
              onChange={(e) =>
                setCustomRange((c) => ({
                  start: c?.start ?? "", end: e.target.value,
                }))
              }
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs"
            />
          </div>
        )}
      </div>

      {error && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="performance-error"
        >
          {error}
        </div>
      )}

      {!error && !loading && perfRows.length === 0 && (
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
          data-testid="performance-empty"
        >
          No closed trades in this window for the selected mode.
        </div>
      )}

      {perfRows.length > 0 && (
        <StrategyComparisonTable rows={perfRows} mode={mode} />
      )}

      {strategyId !== "all" && (
        <>
          <TradeLogTable
            rows={trades}
            filenamePrefix={mode}
            emptyMessage="No closed trades in this window."
          />
          {perTickerRows.length > 0 && (
            <PerTickerBreakdown rows={perTickerRows} />
          )}
        </>
      )}
    </div>
  );
}

// Max DD% only has a true, capital-based value for backtest/
// walkforward runs (algo.runs.summary_json.max_drawdown_pct, from
// a known initial_capital_inr). Paper/live have no per-strategy
// capital baseline (algo.user_budget.allocated_inr is account-wide,
// pooled across every live strategy) -- backend/algo/routes/
// performance.py leaves max_drawdown_pct null for those two modes.
// Hide the column entirely there rather than show an always-blank
// "--" filler that implies a failed computation.
const MODES_WITH_MAX_DRAWDOWN: PerformanceMode[] = [
  "backtest", "walkforward",
];

function StrategyComparisonTable({
  rows, mode,
}: {
  rows: StrategyPerfRow[];
  mode: PerformanceMode;
}) {
  const showMaxDrawdown = MODES_WITH_MAX_DRAWDOWN.includes(mode);
  return (
    <div
      className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700"
      data-testid="performance-strategy-comparison-table"
    >
      <table className="min-w-full text-sm">
        <thead className="bg-slate-50 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">Strategy</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Trades</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Win rate</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Total PnL</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Biggest win</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Biggest loss</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Profit factor</th>
            {showMaxDrawdown && (
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Max DD%</th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.strategy_id}
              data-testid={`performance-strategy-row-${r.strategy_id}`}
              className="border-t border-slate-200 dark:border-slate-700"
            >
              <td className="px-3 py-1.5 font-medium text-slate-900 dark:text-slate-100">{r.strategy_name}</td>
              <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{r.total_trades}</td>
              <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{fmtPct(r.win_rate_pct)}</td>
              <td className={`px-3 py-1.5 text-right font-medium ${r.total_pnl_inr >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>{fmtInr(r.total_pnl_inr)}</td>
              <td className="px-3 py-1.5 text-right text-emerald-600 dark:text-emerald-400">{r.biggest_win ? `${r.biggest_win.ticker} ${fmtInr(r.biggest_win.pnl_inr)}` : "—"}</td>
              <td className="px-3 py-1.5 text-right text-rose-600 dark:text-rose-400">{r.biggest_loss ? `${r.biggest_loss.ticker} ${fmtInr(r.biggest_loss.pnl_inr)}` : "—"}</td>
              <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{r.profit_factor ?? "—"}</td>
              {showMaxDrawdown && (
                <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{fmtPct(r.max_drawdown_pct)}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PerTickerBreakdown({
  rows,
}: {
  rows: { ticker: string; trades: number; pnl: number; wins: number }[];
}) {
  return (
    <div data-testid="performance-per-ticker-breakdown" className="space-y-1.5">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        Per-ticker breakdown
      </h3>
      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">Ticker</th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Trades</th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Win rate</th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">PnL</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker} className="border-t border-slate-200 dark:border-slate-700">
                <td className="px-3 py-1.5 font-medium text-slate-900 dark:text-slate-100">{r.ticker}</td>
                <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{r.trades}</td>
                <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{fmtPct((r.wins / r.trades) * 100)}</td>
                <td className={`px-3 py-1.5 text-right font-medium ${r.pnl >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>{fmtInr(r.pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
