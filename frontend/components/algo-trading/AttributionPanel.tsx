"use client";
/**
 * AttributionPanel — REGIME-6.
 *
 * Two inner sub-tabs:
 *   1. Daily Brinson — table of per-day allocation/selection/
 *      interaction decomposition + total active return.
 *   2. Trade Reasons — table of per-trade entry/exit context:
 *      regime at entry, factor exposures, exit reason, P/L.
 *
 * Mounts in the Live segment of PaperTab below ActiveRunsPanel.
 * The panel only requests data once a strategy is selected; it
 * still renders an empty-state shell when `strategyId` is
 * absent so we don't crash the parent.
 *
 * UX patterns mirrored from KitePostbackPanel + FactorScoresTab:
 *   - Loading shell carries text (Lighthouse FCP heuristic).
 *   - Mock-data warning chip surfaced in the panel header per
 *     CLAUDE.md §5.5 (transparency over silent rendering).
 */

import { useState } from "react";

import {
  useAttributionDaily,
  useAttributionRegression,
  useAttributionTrades,
  type AttributionDailyRow,
  type AttributionTradeRow,
} from "@/hooks/useAttribution";

// ── Helpers ─────────────────────────────────────────────────

function topSector(map: Record<string, number> | undefined): string {
  if (!map || Object.keys(map).length === 0) return "—";
  let best: [string, number] | null = null;
  for (const [k, v] of Object.entries(map)) {
    const cur = Math.abs(Number(v) || 0);
    if (best === null || cur > Math.abs(best[1])) {
      best = [k, Number(v) || 0];
    }
  }
  return best ? `${best[0]} (${best[1] >= 0 ? "+" : ""}${(best[1] * 100).toFixed(2)}%)` : "—";
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function fmtPctRaw(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(2)}%`;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso;
}

// ── Sub-components ──────────────────────────────────────────

function LoadingShell({ label }: { label: string }) {
  return (
    <div
      className="space-y-1 p-3"
      aria-busy="true"
      aria-label={label}
      data-testid="attribution-loading"
    >
      <p className="text-xs text-slate-500">{label}</p>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-7 animate-pulse rounded bg-slate-100
            dark:bg-slate-800"
        />
      ))}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div
      className="rounded-md border border-slate-200 bg-slate-50 p-3
        text-xs text-slate-600 dark:border-slate-700
        dark:bg-slate-900/50 dark:text-slate-400"
      data-testid="attribution-empty"
    >
      {message}
    </div>
  );
}

function MockChip() {
  return (
    <span
      className="ml-2 inline-flex items-center rounded-full
        bg-amber-100 px-2 py-0.5 text-[10px] font-medium
        text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
      data-testid="attribution-mock-chip"
      title={
        "Latest factor regression uses mock factor returns " +
        "(real Indian Fama-French wiring lands in v3.1)."
      }
    >
      mock factor data
    </span>
  );
}

// ── Brinson sub-tab ─────────────────────────────────────────

function DailyBrinsonTab({ strategyId }: { strategyId: string | null }) {
  const { rows, loading, error } = useAttributionDaily(strategyId, 30);

  if (!strategyId) {
    return (
      <EmptyState message="Select a strategy above to load daily attribution." />
    );
  }
  if (loading && rows.length === 0) {
    return <LoadingShell label="Loading Brinson decomposition…" />;
  }
  if (error) {
    return (
      <p className="p-3 text-xs text-rose-600 dark:text-rose-400">
        Could not load Brinson rows: {error.message}
      </p>
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState message="No Brinson rows yet. The daily job runs after market close (IST)." />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table
        className="min-w-full text-xs"
        data-testid="attribution-brinson-table"
      >
        <thead>
          <tr className="border-b border-slate-100 dark:border-slate-800">
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Date
            </th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Total active return
            </th>
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Top allocation sector
            </th>
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Top selection sector
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row: AttributionDailyRow) => (
            <tr
              key={`${row.strategy_id}-${row.bar_date}`}
              className="border-b border-slate-100 hover:bg-slate-50
                dark:border-slate-800 dark:hover:bg-slate-800/50"
            >
              <td className="px-3 py-1.5 font-mono text-[11px] text-slate-700 dark:text-slate-300">
                {fmtDate(row.bar_date)}
              </td>
              <td
                className={
                  "px-3 py-1.5 text-right font-medium " +
                  (row.total_active_return >= 0
                    ? "text-emerald-700 dark:text-emerald-400"
                    : "text-rose-700 dark:text-rose-400")
                }
              >
                {fmtPctRaw(row.total_active_return)}
              </td>
              <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                {topSector(row.brinson_alloc)}
              </td>
              <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                {topSector(row.brinson_select)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Trades sub-tab ──────────────────────────────────────────

function TradeReasonsTab({ strategyId }: { strategyId: string | null }) {
  const { rows, loading, error } = useAttributionTrades(strategyId);

  if (!strategyId) {
    return (
      <EmptyState message="Select a strategy above to load trade reasons." />
    );
  }
  if (loading && rows.length === 0) {
    return <LoadingShell label="Loading trade reasons…" />;
  }
  if (error) {
    return (
      <p className="p-3 text-xs text-rose-600 dark:text-rose-400">
        Could not load trade reasons: {error.message}
      </p>
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState message="No closed trades today. Reasons appear once entry + exit fills land." />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table
        className="min-w-full text-xs"
        data-testid="attribution-trades-table"
      >
        <thead>
          <tr className="border-b border-slate-100 dark:border-slate-800">
            <th className="sticky left-0 bg-white px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400 dark:bg-slate-900">
              Ticker
            </th>
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Opened
            </th>
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Closed
            </th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wide text-slate-400">
              P/L %
            </th>
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Regime
            </th>
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Reason
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row: AttributionTradeRow, idx: number) => (
            <tr
              key={`${row.ticker}-${row.closed_at ?? idx}`}
              className="border-b border-slate-100 hover:bg-slate-50
                dark:border-slate-800 dark:hover:bg-slate-800/50"
            >
              <td className="sticky left-0 bg-white px-3 py-1.5 font-mono text-xs font-semibold text-slate-900 dark:bg-slate-900 dark:text-slate-100">
                {row.ticker}
              </td>
              <td className="px-3 py-1.5 font-mono text-[11px] text-slate-600 dark:text-slate-400">
                {fmtDate(row.opened_at)}
              </td>
              <td className="px-3 py-1.5 font-mono text-[11px] text-slate-600 dark:text-slate-400">
                {fmtDate(row.closed_at)}
              </td>
              <td
                className={
                  "px-3 py-1.5 text-right font-medium " +
                  (row.pnl_pct >= 0
                    ? "text-emerald-700 dark:text-emerald-400"
                    : "text-rose-700 dark:text-rose-400")
                }
              >
                {fmtPct(row.pnl_pct)}
              </td>
              <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                {row.entry_regime ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-slate-600 dark:text-slate-400">
                {row.reason_text || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main panel ──────────────────────────────────────────────

type SubTab = "brinson" | "trades";

interface AttributionPanelProps {
  /** Strategy selected in the Live segment of PaperTab. May be
   *  empty — the panel renders a guidance state in that case. */
  strategyId: string | null;
}

export function AttributionPanel({ strategyId }: AttributionPanelProps) {
  const [tab, setTab] = useState<SubTab>("brinson");
  // The mock-data signal lives on the regression endpoint (the
  // factor regression is the v3-mocked piece). Brinson + trade
  // reasons use real algo.events / algo.attribution_daily data.
  const { latest: latestRegression } = useAttributionRegression(
    strategyId,
  );
  const showMockChip = Boolean(latestRegression?.mock_data);

  return (
    <div
      className="rounded-md border border-slate-200
        dark:border-slate-700"
      data-testid="attribution-panel"
    >
      <div
        className="flex items-center justify-between border-b
          border-slate-200 px-3 py-2 dark:border-slate-700"
      >
        <h4
          className="flex items-center text-xs font-semibold
            uppercase tracking-wide text-slate-500
            dark:text-slate-400"
        >
          Attribution
          {showMockChip && <MockChip />}
        </h4>

        <div
          className="inline-flex rounded-md border border-slate-200
            text-[11px] font-medium dark:border-slate-700"
          role="tablist"
          aria-label="Attribution sub-tabs"
          data-testid="attribution-subtab-strip"
        >
          {([
            { id: "brinson", label: "Daily Brinson" },
            { id: "trades", label: "Trade Reasons" },
          ] as const).map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={
                "px-3 py-1 " +
                (tab === t.id
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-slate-700 hover:bg-slate-50 " +
                    "dark:bg-slate-800 dark:text-slate-200 " +
                    "dark:hover:bg-slate-700")
              }
              data-testid={`attribution-subtab-${t.id}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-3">
        {tab === "brinson" && (
          <DailyBrinsonTab strategyId={strategyId} />
        )}
        {tab === "trades" && (
          <TradeReasonsTab strategyId={strategyId} />
        )}
      </div>
    </div>
  );
}
