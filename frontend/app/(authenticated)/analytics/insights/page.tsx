"use client";
/**
 * Native Insights page — replaces the Dash iframe.
 *
 * 7 tabs: Screener, Price Targets, Dividends,
 * Risk Metrics, Sectors, Correlation, Quarterly.
 */

import {
  useState,
  useMemo,
  useCallback,
  useEffect,
  useRef,
  Suspense,
} from "react";
import {
  useSearchParams,
  useRouter,
} from "next/navigation";
import {
  useScreener,
  useTargets,
  useDividends,
  useRisk,
  useSectors,
  useCorrelation,
  useQuarterly,
  usePiotroski,
} from "@/hooks/useInsightsData";
import {
  InsightsTable,
  type Column,
} from "@/components/insights/InsightsTable";
import { InsightsFilters } from "@/components/insights/InsightsFilters";
import {
  ColumnSelector,
} from "@/components/insights/ColumnSelector";
import {
  useColumnSelection,
} from "@/lib/useColumnSelection";
import {
  downloadCsv,
  type CsvColumn,
} from "@/lib/downloadCsv";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { CorrelationHeatmap } from "@/components/charts/CorrelationHeatmap";
import { PiotroskiBadge } from "@/components/insights/PiotroskiBadge";
import { usePortfolio } from "@/hooks/usePortfolio";
import { WidgetSkeleton } from "@/components/widgets/WidgetSkeleton";
import { WidgetError } from "@/components/widgets/WidgetError";
import type {
  ScreenerRow,
  TargetRow,
  DividendRow,
  RiskRow,
  SectorRow,
  QuarterlyRow,
  PiotroskiRow,
} from "@/lib/types";

// ---------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------

type TabId =
  | "screener"
  | "targets"
  | "dividends"
  | "risk"
  | "sectors"
  | "correlation"
  | "quarterly"
  | "piotroski"
  | "screenql";

const TABS: { id: TabId; label: string }[] = [
  { id: "screener", label: "Screener" },
  { id: "risk", label: "Risk Metrics" },
  { id: "sectors", label: "Sectors" },
  { id: "targets", label: "Price Targets" },
  { id: "dividends", label: "Dividends" },
  { id: "correlation", label: "Correlation" },
  { id: "quarterly", label: "Quarterly" },
  { id: "piotroski", label: "Piotroski F-Score" },
  { id: "screenql", label: "ScreenQL" },
];

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

const CURRENCY_MAP: Record<string, string> = {
  USD: "$",
  INR: "\u20B9",
  GBP: "\u00A3",
  EUR: "\u20AC",
  JPY: "\u00A5",
  CNY: "\u00A5",
  AUD: "A$",
  CAD: "CA$",
};

function fmtNum(
  v: number | null | undefined,
  decimals = 2,
): string {
  if (v == null) return "\u2014";
  return v.toFixed(decimals);
}

function fmtPct(
  v: number | null | undefined,
): string {
  if (v == null) return "\u2014";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function pctColor(
  v: number | null | undefined,
): string {
  if (v == null) return "";
  return v >= 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

function signalBadge(
  s: string | null | undefined,
): React.ReactNode {
  if (!s) return "\u2014";
  const lower = s.toLowerCase();
  let cls =
    "px-1.5 py-0.5 rounded text-xs font-medium ";
  if (
    lower.includes("bull") ||
    lower.includes("above")
  ) {
    cls +=
      "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
  } else if (
    lower.includes("bear") ||
    lower.includes("below") ||
    lower.includes("over")
  ) {
    cls +=
      "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
  } else {
    cls +=
      "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300";
  }
  return <span className={cls}>{s}</span>;
}

function sentimentBadge(
  s: string | null | undefined,
): React.ReactNode {
  if (!s) return "\u2014";
  const lower = s.toLowerCase();
  let cls =
    "px-1.5 py-0.5 rounded text-xs font-medium ";
  if (lower === "bullish") {
    cls +=
      "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
  } else if (lower === "bearish") {
    cls +=
      "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
  } else {
    cls +=
      "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300";
  }
  return <span className={cls}>{s}</span>;
}

// ---------------------------------------------------------------
// Filter helper
// ---------------------------------------------------------------

function applyFilters<
  T extends {
    market: string;
    sector?: string | null;
    ticker?: string;
  },
>(
  rows: T[],
  market: string,
  sector: string,
  ticker: string,
  rsiFilter?: string,
): T[] {
  let filtered = rows;
  if (market !== "all") {
    filtered = filtered.filter(
      (r) => r.market === market,
    );
  }
  if (sector !== "all") {
    filtered = filtered.filter(
      (r) => r.sector === sector,
    );
  }
  if (ticker !== "all" && "ticker" in filtered[0]!) {
    filtered = filtered.filter(
      (r) =>
        (r as Record<string, unknown>).ticker ===
        ticker,
    );
  }
  if (rsiFilter && rsiFilter !== "all") {
    filtered = filtered.filter((r) => {
      const rsi = (r as Record<string, unknown>)
        .rsi_14 as number | null;
      if (rsi == null) return false;
      if (rsiFilter === "oversold") return rsi < 30;
      if (rsiFilter === "overbought") return rsi > 70;
      return rsi >= 30 && rsi <= 70;
    });
  }
  return filtered;
}

// ---------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------

const screenerCols: Column<ScreenerRow>[] = [
  { key: "ticker", label: "Ticker" },
  {
    key: "price",
    label: "Price",
    numeric: true,
    render: (r) => fmtNum(r.price),
  },
  {
    key: "rsi_14",
    label: "RSI",
    numeric: true,
    render: (r) => fmtNum(r.rsi_14),
  },
  {
    key: "rsi_signal",
    label: "RSI Signal",
    render: (r) => signalBadge(r.rsi_signal),
  },
  {
    key: "sentiment_score",
    label: "Sentiment",
    numeric: true,
    tooltip:
      "LLM-scored sentiment from recent news. " +
      "Range: -1 (bearish) to +1 (bullish). " +
      "Each headline scored via Groq LLM, " +
      "averaged across Yahoo Finance, Reuters " +
      "and other sources.",
    render: (r) => {
      if (r.sentiment_score == null) return "—";
      const s = r.sentiment_score;
      const label =
        s >= 0.3
          ? "Bullish"
          : s <= -0.3
            ? "Bearish"
            : "Neutral";
      const color =
        s >= 0.3
          ? "text-emerald-600 dark:text-emerald-400"
          : s <= -0.3
            ? "text-red-600 dark:text-red-400"
            : "text-gray-600 dark:text-gray-400";
      return (
        <span
          className={`${color} cursor-help`}
          title={`Score: ${s.toFixed(3)} from ${r.sentiment_headlines ?? 0} headlines`}
        >
          {label}
          <span className="ml-1 text-[10px] opacity-60">
            {s >= 0 ? "+" : ""}
            {s.toFixed(2)}
          </span>
        </span>
      );
    },
  },
  {
    key: "macd_signal",
    label: "MACD",
    render: (r) => signalBadge(r.macd_signal),
  },
  {
    key: "sma_200_signal",
    label: "vs SMA 200",
    render: (r) => signalBadge(r.sma_200_signal),
  },
  {
    key: "annualized_return_pct",
    label: "Ann. Ret %",
    numeric: true,
    render: (r) => (
      <span
        className={pctColor(
          r.annualized_return_pct,
        )}
      >
        {fmtPct(r.annualized_return_pct)}
      </span>
    ),
  },
  {
    key: "annualized_volatility_pct",
    label: "Vol %",
    numeric: true,
    render: (r) =>
      fmtNum(r.annualized_volatility_pct),
  },
  {
    key: "sharpe_ratio",
    label: "Sharpe",
    numeric: true,
    render: (r) => fmtNum(r.sharpe_ratio),
  },
  {
    key: "peg_ratio",
    label: "PEG (T)",
    numeric: true,
    render: (r) => fmtNum(r.peg_ratio),
  },
  {
    key: "peg_ratio_yf",
    label: "PEG (YF)",
    numeric: true,
    render: (r) => fmtNum(r.peg_ratio_yf),
  },
  {
    key: "peg_ratio_ttm",
    label: "PEG (Q)",
    numeric: true,
    render: (r) => fmtNum(r.peg_ratio_ttm),
  },
  // ── Identity / context ──────────────────────────
  {
    key: "company_name",
    label: "Company",
    render: (r) => r.company_name ?? "—",
  },
  {
    key: "sector",
    label: "Sector",
    render: (r) => r.sector ?? "—",
  },
  {
    key: "industry",
    label: "Industry",
    render: (r) => r.industry ?? "—",
  },
  {
    key: "currency",
    label: "Currency",
    render: (r) => r.currency ?? "—",
  },
  // ── Pricing ─────────────────────────────────────
  {
    key: "current_price",
    label: "Live Price",
    numeric: true,
    render: (r) => fmtNum(r.current_price),
  },
  {
    key: "week_52_high",
    label: "52W High",
    numeric: true,
    render: (r) => fmtNum(r.week_52_high),
  },
  {
    key: "week_52_low",
    label: "52W Low",
    numeric: true,
    render: (r) => fmtNum(r.week_52_low),
  },
  // ── Valuation ───────────────────────────────────
  {
    key: "market_cap",
    label: "Market Cap",
    numeric: true,
    render: (r) =>
      r.market_cap != null
        ? (r.market_cap / 1e7).toFixed(0)
        : "—",
  },
  {
    key: "pe_ratio",
    label: "P/E",
    numeric: true,
    render: (r) => fmtNum(r.pe_ratio),
  },
  {
    key: "price_to_book",
    label: "P/B",
    numeric: true,
    render: (r) => fmtNum(r.price_to_book),
  },
  {
    key: "dividend_yield",
    label: "Div Yield",
    numeric: true,
    render: (r) =>
      r.dividend_yield != null
        ? `${(r.dividend_yield * 100).toFixed(2)}%`
        : "—",
  },
  // ── Profitability ───────────────────────────────
  {
    key: "profit_margins",
    label: "Profit Margin",
    numeric: true,
    render: (r) =>
      r.profit_margins != null
        ? `${(r.profit_margins * 100).toFixed(2)}%`
        : "—",
  },
  {
    key: "earnings_growth",
    label: "EPS Growth",
    numeric: true,
    render: (r) =>
      r.earnings_growth != null
        ? `${(r.earnings_growth * 100).toFixed(1)}%`
        : "—",
  },
  {
    key: "revenue_growth",
    label: "Rev Growth",
    numeric: true,
    render: (r) =>
      r.revenue_growth != null
        ? `${(r.revenue_growth * 100).toFixed(1)}%`
        : "—",
  },
  {
    key: "eps",
    label: "EPS",
    numeric: true,
    render: (r) => fmtNum(r.eps),
  },
  {
    key: "revenue",
    label: "Revenue (Cr)",
    numeric: true,
    render: (r) =>
      r.revenue != null
        ? (r.revenue / 1e7).toFixed(0)
        : "—",
  },
  {
    key: "net_income",
    label: "Net Income (Cr)",
    numeric: true,
    render: (r) =>
      r.net_income != null
        ? (r.net_income / 1e7).toFixed(0)
        : "—",
  },
  // ── Risk ────────────────────────────────────────
  {
    key: "max_drawdown_pct",
    label: "Max DD %",
    numeric: true,
    render: (r) => fmtNum(r.max_drawdown_pct),
  },
  {
    key: "beta",
    label: "Beta",
    numeric: true,
    render: (r) => fmtNum(r.beta),
  },
  // ── Quality ─────────────────────────────────────
  {
    key: "piotroski_score",
    label: "Piotroski",
    numeric: true,
    render: (r) =>
      r.piotroski_score != null
        ? String(r.piotroski_score)
        : "—",
  },
  {
    key: "piotroski_label",
    label: "Piotroski Rating",
    render: (r) => r.piotroski_label ?? "—",
  },
  {
    key: "forecast_confidence",
    label: "Forecast Conf.",
    numeric: true,
    render: (r) =>
      r.forecast_confidence != null
        ? r.forecast_confidence.toFixed(2)
        : "—",
  },
  // ── Forecast ────────────────────────────────────
  {
    key: "target_3m_pct",
    label: "3M Target %",
    numeric: true,
    render: (r) =>
      r.target_3m_pct != null
        ? `${r.target_3m_pct.toFixed(1)}%`
        : "—",
  },
  {
    key: "target_6m_pct",
    label: "6M Target %",
    numeric: true,
    render: (r) =>
      r.target_6m_pct != null
        ? `${r.target_6m_pct.toFixed(1)}%`
        : "—",
  },
  {
    key: "target_9m_pct",
    label: "9M Target %",
    numeric: true,
    render: (r) =>
      r.target_9m_pct != null
        ? `${r.target_9m_pct.toFixed(1)}%`
        : "—",
  },
  {
    key: "action",
    label: "Action",
    sortable: false,
    render: (r) => (
      <div className="flex items-center gap-1.5">
        <button
          title="Stock Analysis"
          onClick={() =>
            window.open(
              `/analytics/analysis?ticker=${encodeURIComponent(r.ticker)}&tab=analysis`,
              "_blank",
            )
          }
          className="flex h-7 w-7 items-center
            justify-center rounded-md border
            border-gray-200 text-gray-400
            transition-all hover:border-indigo-400
            hover:bg-indigo-50 hover:text-indigo-600
            dark:border-gray-700 dark:text-gray-500
            dark:hover:border-indigo-500
            dark:hover:bg-indigo-500/10
            dark:hover:text-indigo-400"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-3.5 w-3.5"
          >
            <path d="M15.5 2A1.5 1.5 0 0014 3.5v13a1.5 1.5 0 001.5 1.5h1a1.5 1.5 0 001.5-1.5v-13A1.5 1.5 0 0016.5 2h-1zM9.5 6A1.5 1.5 0 008 7.5v9A1.5 1.5 0 009.5 18h1a1.5 1.5 0 001.5-1.5v-9A1.5 1.5 0 0010.5 6h-1zM3.5 10A1.5 1.5 0 002 11.5v5A1.5 1.5 0 003.5 18h1A1.5 1.5 0 006 16.5v-5A1.5 1.5 0 004.5 10h-1z" />
          </svg>
        </button>
        <button
          title="Stock Forecast"
          onClick={() =>
            window.open(
              `/analytics/analysis?ticker=${encodeURIComponent(r.ticker)}&tab=forecast`,
              "_blank",
            )
          }
          className="flex h-7 w-7 items-center
            justify-center rounded-md border
            border-gray-200 text-gray-400
            transition-all hover:border-indigo-400
            hover:bg-indigo-50 hover:text-indigo-600
            dark:border-gray-700 dark:text-gray-500
            dark:hover:border-indigo-500
            dark:hover:bg-indigo-500/10
            dark:hover:text-indigo-400"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-3.5 w-3.5"
          >
            <path
              fillRule="evenodd"
              d="M12.577 4.878a.75.75 0 01.919-.53l4.78 1.281a.75.75 0 01.531.919l-1.281 4.78a.75.75 0 01-1.449-.387l.81-3.022a19.407 19.407 0 00-5.594 5.203.75.75 0 01-1.139.093L7 10.06l-4.72 4.72a.75.75 0 01-1.06-1.06l5.25-5.25a.75.75 0 011.06 0l3.046 3.046a20.902 20.902 0 015.441-5.185l-2.752.736a.75.75 0 01-.919-.53z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      </div>
    ),
  },
];

const targetCols: Column<TargetRow>[] = [
  { key: "ticker", label: "Ticker" },
  {
    key: "run_date",
    label: "Run Date",
    render: (r) => r.run_date ?? "\u2014",
  },
  {
    key: "current_price",
    label: "Price",
    numeric: true,
    render: (r) => fmtNum(r.current_price),
  },
  {
    key: "target_3m_price",
    label: "3m Target",
    numeric: true,
    render: (r) => (
      <span>
        {fmtNum(r.target_3m_price)}{" "}
        <span
          className={`text-xs ${pctColor(r.target_3m_pct)}`}
        >
          {fmtPct(r.target_3m_pct)}
        </span>
      </span>
    ),
  },
  {
    key: "target_6m_price",
    label: "6m Target",
    numeric: true,
    render: (r) => (
      <span>
        {fmtNum(r.target_6m_price)}{" "}
        <span
          className={`text-xs ${pctColor(r.target_6m_pct)}`}
        >
          {fmtPct(r.target_6m_pct)}
        </span>
      </span>
    ),
  },
  {
    key: "target_9m_price",
    label: "9m Target",
    numeric: true,
    render: (r) => (
      <span>
        {fmtNum(r.target_9m_price)}{" "}
        <span
          className={`text-xs ${pctColor(r.target_9m_pct)}`}
        >
          {fmtPct(r.target_9m_pct)}
        </span>
      </span>
    ),
  },
  {
    key: "sentiment",
    label: "Sentiment",
    render: (r) => sentimentBadge(r.sentiment),
  },
];

const dividendCols: Column<DividendRow>[] = [
  { key: "ticker", label: "Ticker" },
  {
    key: "ex_date",
    label: "Ex-Date",
    render: (r) => r.ex_date ?? "\u2014",
  },
  {
    key: "amount",
    label: "Amount",
    numeric: true,
    render: (r) => {
      const sym =
        CURRENCY_MAP[r.currency] ?? r.currency;
      return r.amount != null
        ? `${sym}${r.amount.toFixed(4)}`
        : "\u2014";
    },
  },
  { key: "currency", label: "Currency" },
];

const riskCols: Column<RiskRow>[] = [
  { key: "ticker", label: "Ticker" },
  {
    key: "annualized_return_pct",
    label: "Ann. Ret %",
    numeric: true,
    render: (r) => (
      <span
        className={pctColor(
          r.annualized_return_pct,
        )}
      >
        {fmtPct(r.annualized_return_pct)}
      </span>
    ),
  },
  {
    key: "annualized_volatility_pct",
    label: "Vol %",
    numeric: true,
    render: (r) =>
      fmtNum(r.annualized_volatility_pct),
  },
  {
    key: "sharpe_ratio",
    label: "Sharpe",
    numeric: true,
    render: (r) => fmtNum(r.sharpe_ratio),
  },
  {
    key: "max_drawdown_pct",
    label: "Max DD %",
    numeric: true,
    render: (r) => (
      <span className="text-red-600 dark:text-red-400">
        {fmtNum(r.max_drawdown_pct)}
      </span>
    ),
  },
  {
    key: "max_drawdown_days",
    label: "DD Days",
    numeric: true,
    render: (r) =>
      r.max_drawdown_days?.toString() ?? "\u2014",
  },
  {
    key: "bull_phase_pct",
    label: "Bull %",
    numeric: true,
    render: (r) => fmtNum(r.bull_phase_pct),
  },
  {
    key: "bear_phase_pct",
    label: "Bear %",
    numeric: true,
    render: (r) => fmtNum(r.bear_phase_pct),
  },
  {
    key: "action",
    label: "Action",
    sortable: false,
    render: (r) => (
      <div className="flex items-center gap-1.5">
        <button
          title="Stock Analysis"
          onClick={() =>
            window.open(
              `/analytics/analysis?ticker=${encodeURIComponent(r.ticker)}&tab=analysis`,
              "_blank",
            )
          }
          className="flex h-7 w-7 items-center
            justify-center rounded-md border
            border-gray-200 text-gray-400
            transition-all hover:border-indigo-400
            hover:bg-indigo-50 hover:text-indigo-600
            dark:border-gray-700 dark:text-gray-500
            dark:hover:border-indigo-500
            dark:hover:bg-indigo-500/10
            dark:hover:text-indigo-400"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
            <path d="M15.5 2A1.5 1.5 0 0014 3.5v13a1.5 1.5 0 001.5 1.5h1a1.5 1.5 0 001.5-1.5v-13A1.5 1.5 0 0016.5 2h-1zM9.5 6A1.5 1.5 0 008 7.5v9A1.5 1.5 0 009.5 18h1a1.5 1.5 0 001.5-1.5v-9A1.5 1.5 0 0010.5 6h-1zM3.5 10A1.5 1.5 0 002 11.5v5A1.5 1.5 0 003.5 18h1A1.5 1.5 0 006 16.5v-5A1.5 1.5 0 004.5 10h-1z" />
          </svg>
        </button>
        <button
          title="Stock Forecast"
          onClick={() =>
            window.open(
              `/analytics/analysis?ticker=${encodeURIComponent(r.ticker)}&tab=forecast`,
              "_blank",
            )
          }
          className="flex h-7 w-7 items-center
            justify-center rounded-md border
            border-gray-200 text-gray-400
            transition-all hover:border-indigo-400
            hover:bg-indigo-50 hover:text-indigo-600
            dark:border-gray-700 dark:text-gray-500
            dark:hover:border-indigo-500
            dark:hover:bg-indigo-500/10
            dark:hover:text-indigo-400"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
            <path fillRule="evenodd" d="M12.577 4.878a.75.75 0 01.919-.53l4.78 1.281a.75.75 0 01.531.919l-1.281 4.78a.75.75 0 01-1.449-.387l.81-3.022a19.407 19.407 0 00-5.594 5.203.75.75 0 01-1.139.093L7 10.06l-4.72 4.72a.75.75 0 01-1.06-1.06l5.25-5.25a.75.75 0 011.06 0l3.046 3.046a20.902 20.902 0 015.441-5.185l-2.752.736a.75.75 0 01-.919-.53z" clipRule="evenodd" />
          </svg>
        </button>
      </div>
    ),
  },
];

const sectorCols: Column<SectorRow>[] = [
  { key: "sector", label: "Sector" },
  {
    key: "stock_count",
    label: "Stocks",
    numeric: true,
  },
  {
    key: "avg_return_pct",
    label: "Avg Ret %",
    numeric: true,
    render: (r) => (
      <span className={pctColor(r.avg_return_pct)}>
        {fmtPct(r.avg_return_pct)}
      </span>
    ),
  },
  {
    key: "avg_sharpe",
    label: "Avg Sharpe",
    numeric: true,
    render: (r) => fmtNum(r.avg_sharpe),
  },
  {
    key: "avg_volatility_pct",
    label: "Avg Vol %",
    numeric: true,
    render: (r) => fmtNum(r.avg_volatility_pct),
  },
];

const piotroskiCols: Column<PiotroskiRow>[] = [
  { key: "ticker", label: "Ticker" },
  {
    key: "company_name",
    label: "Company",
    render: (r) => r.company_name ?? "\u2014",
  },
  {
    key: "total_score",
    label: "Score",
    numeric: true,
    render: (r) => (
      <PiotroskiBadge
        score={r.total_score}
        label={r.label}
      />
    ),
  },
  {
    key: "label",
    label: "Rating",
    render: (r) => r.label,
  },
  {
    key: "sector",
    label: "Sector",
    render: (r) => r.sector ?? "\u2014",
  },
  {
    key: "market_cap",
    label: "MCap (Cr)",
    numeric: true,
    render: (r) =>
      r.market_cap != null
        ? (r.market_cap / 1e7).toFixed(0)
        : "\u2014",
  },
  {
    key: "revenue",
    label: "Rev (Cr)",
    numeric: true,
    render: (r) =>
      r.revenue != null
        ? (r.revenue / 1e7).toFixed(0)
        : "\u2014",
  },
  {
    key: "avg_volume",
    label: "Avg Vol",
    numeric: true,
    render: (r) =>
      r.avg_volume != null
        ? r.avg_volume.toLocaleString()
        : "\u2014",
  },
  {
    key: "action",
    label: "Action",
    sortable: false,
    render: (r) => (
      <button
        title="Stock Analysis"
        onClick={() =>
          window.open(
            `/analytics/analysis?ticker=${encodeURIComponent(r.ticker)}&tab=analysis`,
            "_blank",
          )
        }
        className="flex h-7 w-7 items-center
          justify-center rounded-md border
          border-gray-200 text-gray-400
          transition-all hover:border-indigo-400
          hover:bg-indigo-50 hover:text-indigo-600
          dark:border-gray-700 dark:text-gray-500
          dark:hover:border-indigo-500
          dark:hover:bg-indigo-500/10
          dark:hover:text-indigo-400"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="h-3.5 w-3.5"
        >
          <path d="M15.5 2A1.5 1.5 0 0014 3.5v13a1.5 1.5 0 001.5 1.5h1a1.5 1.5 0 001.5-1.5v-13A1.5 1.5 0 0016.5 2h-1zM9.5 6A1.5 1.5 0 008 7.5v9A1.5 1.5 0 009.5 18h1a1.5 1.5 0 001.5-1.5v-9A1.5 1.5 0 0010.5 6h-1zM3.5 10A1.5 1.5 0 002 11.5v5A1.5 1.5 0 003.5 18h1A1.5 1.5 0 006 16.5v-5A1.5 1.5 0 004.5 10h-1z" />
        </svg>
      </button>
    ),
  },
];

// ---------------------------------------------------------------
// Screener column catalog (for ColumnSelector popover)
// ASETPLTFRM-333
// ---------------------------------------------------------------

/** Column key → category mapping shown in the selector. */
const SCREENER_COL_CATALOG = [
  { key: "ticker", label: "Ticker", category: "Identity" },
  { key: "company_name", label: "Company", category: "Identity" },
  { key: "sector", label: "Sector", category: "Identity" },
  { key: "industry", label: "Industry", category: "Identity" },
  { key: "currency", label: "Currency", category: "Identity" },
  { key: "price", label: "Price", category: "Pricing" },
  { key: "current_price", label: "Live Price", category: "Pricing" },
  { key: "week_52_high", label: "52W High", category: "Pricing" },
  { key: "week_52_low", label: "52W Low", category: "Pricing" },
  { key: "market_cap", label: "Market Cap", category: "Valuation" },
  { key: "pe_ratio", label: "P/E", category: "Valuation" },
  { key: "peg_ratio", label: "PEG (T)", category: "Valuation" },
  { key: "peg_ratio_yf", label: "PEG (YF)", category: "Valuation" },
  { key: "peg_ratio_ttm", label: "PEG (Q)", category: "Valuation" },
  { key: "price_to_book", label: "P/B", category: "Valuation" },
  { key: "dividend_yield", label: "Div Yield", category: "Valuation" },
  { key: "profit_margins", label: "Profit Margin", category: "Profitability" },
  { key: "earnings_growth", label: "EPS Growth", category: "Profitability" },
  { key: "revenue_growth", label: "Rev Growth", category: "Profitability" },
  { key: "eps", label: "EPS", category: "Profitability" },
  { key: "revenue", label: "Revenue (Cr)", category: "Profitability" },
  { key: "net_income", label: "Net Income (Cr)", category: "Profitability" },
  { key: "annualized_return_pct", label: "Ann. Ret %", category: "Risk" },
  { key: "annualized_volatility_pct", label: "Vol %", category: "Risk" },
  { key: "sharpe_ratio", label: "Sharpe", category: "Risk" },
  { key: "max_drawdown_pct", label: "Max DD %", category: "Risk" },
  { key: "beta", label: "Beta", category: "Risk" },
  { key: "rsi_14", label: "RSI", category: "Technical" },
  { key: "rsi_signal", label: "RSI Signal", category: "Technical" },
  { key: "macd_signal", label: "MACD", category: "Technical" },
  { key: "sma_200_signal", label: "vs SMA 200", category: "Technical" },
  { key: "sentiment_score", label: "Sentiment", category: "Technical" },
  { key: "piotroski_score", label: "Piotroski", category: "Quality" },
  { key: "piotroski_label", label: "Piotroski Rating", category: "Quality" },
  { key: "forecast_confidence", label: "Forecast Conf.", category: "Quality" },
  { key: "target_3m_pct", label: "3M Target %", category: "Forecast" },
  { key: "target_6m_pct", label: "6M Target %", category: "Forecast" },
  { key: "target_9m_pct", label: "9M Target %", category: "Forecast" },
  { key: "action", label: "Action", category: "Identity" },
];

/** Default columns shown on first load (~13, matches
 *  the pre-selector UI so there's zero regression). */
const SCREENER_DEFAULT_COLS = [
  "ticker", "price", "rsi_14", "rsi_signal",
  "sentiment_score", "macd_signal", "sma_200_signal",
  "annualized_return_pct",
  "annualized_volatility_pct", "sharpe_ratio",
  "peg_ratio", "peg_ratio_yf", "peg_ratio_ttm",
  "action",
];

const SCREENER_ALL_COL_KEYS = SCREENER_COL_CATALOG.map(
  (c) => c.key,
);

// ---------------------------------------------------------------
// CSV column definitions (for downloadCsv)
// ---------------------------------------------------------------

const screenerCsvCols: CsvColumn<ScreenerRow>[] = [
  { key: "ticker", header: "Ticker" },
  { key: "price", header: "Price" },
  { key: "rsi_14", header: "RSI" },
  { key: "rsi_signal", header: "RSI Signal" },
  {
    key: "sentiment_score",
    header: "Sentiment",
    format: (v) => (v != null ? String(v) : ""),
  },
  { key: "macd_signal", header: "MACD Signal" },
  { key: "sma_200_signal", header: "vs SMA 200" },
  {
    key: "annualized_return_pct",
    header: "Ann Return %",
  },
  {
    key: "annualized_volatility_pct",
    header: "Vol %",
  },
  { key: "sharpe_ratio", header: "Sharpe" },
  { key: "peg_ratio", header: "PEG (trailing)" },
  { key: "peg_ratio_yf", header: "PEG (yfinance)" },
  { key: "peg_ratio_ttm", header: "PEG (quarterly TTM)" },
  { key: "sector", header: "Sector" },
  // Extended columns (ASETPLTFRM-333) — included in
  // CSV when user toggles them on.
  { key: "company_name", header: "Company" },
  { key: "industry", header: "Industry" },
  { key: "currency", header: "Currency" },
  { key: "current_price", header: "Live Price" },
  { key: "week_52_high", header: "52W High" },
  { key: "week_52_low", header: "52W Low" },
  { key: "market_cap", header: "Market Cap" },
  { key: "pe_ratio", header: "P/E" },
  { key: "price_to_book", header: "P/B" },
  { key: "dividend_yield", header: "Div Yield" },
  { key: "profit_margins", header: "Profit Margin" },
  { key: "earnings_growth", header: "EPS Growth" },
  { key: "revenue_growth", header: "Rev Growth" },
  { key: "eps", header: "EPS" },
  { key: "revenue", header: "Revenue" },
  { key: "net_income", header: "Net Income" },
  { key: "max_drawdown_pct", header: "Max DD %" },
  { key: "beta", header: "Beta" },
  { key: "piotroski_score", header: "Piotroski" },
  { key: "piotroski_label", header: "Piotroski Rating" },
  { key: "forecast_confidence", header: "Forecast Conf." },
  { key: "target_3m_pct", header: "3M Target %" },
  { key: "target_6m_pct", header: "6M Target %" },
  { key: "target_9m_pct", header: "9M Target %" },
];

const riskCsvCols: CsvColumn<RiskRow>[] = [
  { key: "ticker", header: "Ticker" },
  {
    key: "annualized_return_pct",
    header: "Ann Return %",
  },
  {
    key: "annualized_volatility_pct",
    header: "Vol %",
  },
  { key: "sharpe_ratio", header: "Sharpe" },
  { key: "max_drawdown_pct", header: "Max DD %" },
  { key: "max_drawdown_days", header: "DD Days" },
  { key: "bull_phase_pct", header: "Bull %" },
  { key: "bear_phase_pct", header: "Bear %" },
  { key: "sector", header: "Sector" },
];

const sectorCsvCols: CsvColumn<SectorRow>[] = [
  { key: "sector", header: "Sector" },
  { key: "stock_count", header: "Stocks" },
  { key: "avg_return_pct", header: "Avg Return %" },
  { key: "avg_sharpe", header: "Avg Sharpe" },
  {
    key: "avg_volatility_pct",
    header: "Avg Vol %",
  },
];

const targetCsvCols: CsvColumn<TargetRow>[] = [
  { key: "ticker", header: "Ticker" },
  { key: "run_date", header: "Run Date" },
  { key: "current_price", header: "Price" },
  { key: "target_3m_price", header: "3m Target" },
  { key: "target_3m_pct", header: "3m Change %" },
  { key: "target_6m_price", header: "6m Target" },
  { key: "target_6m_pct", header: "6m Change %" },
  { key: "target_9m_price", header: "9m Target" },
  { key: "target_9m_pct", header: "9m Change %" },
  { key: "sentiment", header: "Sentiment" },
];

const dividendCsvCols: CsvColumn<DividendRow>[] = [
  { key: "ticker", header: "Ticker" },
  { key: "ex_date", header: "Ex-Date" },
  { key: "amount", header: "Amount" },
  { key: "currency", header: "Currency" },
];

const quarterlyCsvKeys: (keyof QuarterlyRow)[] = [
  "ticker",
  "quarter_label",
  "revenue",
  "net_income",
  "eps",
  "total_assets",
  "total_equity",
  "operating_cashflow",
  "free_cashflow",
];

const piotroskiCsvCols: CsvColumn<PiotroskiRow>[] = [
  { key: "ticker", header: "Ticker" },
  { key: "company_name", header: "Company" },
  { key: "total_score", header: "Score" },
  { key: "label", header: "Rating" },
  { key: "sector", header: "Sector" },
  {
    key: "market_cap",
    header: "MCap (Cr)",
    format: (v) =>
      v != null
        ? (Number(v) / 1e7).toFixed(0)
        : "",
  },
  {
    key: "revenue",
    header: "Revenue (Cr)",
    format: (v) =>
      v != null
        ? (Number(v) / 1e7).toFixed(0)
        : "",
  },
  { key: "avg_volume", header: "Avg Volume" },
];

// ---------------------------------------------------------------
// Tab content components
// ---------------------------------------------------------------

function ScreenerTab() {
  const data = useScreener();
  const [market, setMarket] = useState("all");
  const [sector, setSector] = useState("all");
  const [rsiFilter, setRsiFilter] = useState("all");
  const [tag, setTag] = useState("all");

  const [
    selectedCols, setSelectedCols, resetCols,
  ] = useColumnSelection(
    "insights.columns.screener",
    SCREENER_DEFAULT_COLS,
    SCREENER_ALL_COL_KEYS,
  );

  // Filter the master screenerCols list to only the
  // keys the user has toggled on, preserving the
  // catalog's canonical ordering.
  const visibleCols = useMemo(() => {
    const s = new Set(selectedCols);
    return screenerCols.filter((c) => s.has(c.key));
  }, [selectedCols]);

  const visibleCsvCols = useMemo(() => {
    const s = new Set(selectedCols);
    return screenerCsvCols.filter((c) =>
      s.has(c.key as string),
    );
  }, [selectedCols]);

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    let rows = applyFilters(
      data.value.rows,
      market,
      sector,
      "all",
      rsiFilter,
    );
    if (tag !== "all") {
      rows = rows.filter(
        (r) => r.tags?.includes(tag),
      );
    }
    return rows;
  }, [data.value, market, sector, rsiFilter, tag]);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return <WidgetError message={data.error} data-testid="insights-error" />;

  return (
    <div className="space-y-4">
      <InsightsFilters
        market={market}
        onMarketChange={setMarket}
        sector={sector}
        onSectorChange={setSector}
        sectors={data.value?.sectors ?? []}
        tag={tag}
        onTagChange={setTag}
        availableTags={data.value?.tags ?? []}
        rsiFilter={rsiFilter}
        onRsiFilterChange={setRsiFilter}
      />
      <div className="flex justify-end">
        <ColumnSelector
          catalog={SCREENER_COL_CATALOG}
          selected={selectedCols}
          onChange={setSelectedCols}
          onReset={resetCols}
          lockedKeys={["ticker"]}
        />
      </div>
      <InsightsTable<ScreenerRow>
        columns={visibleCols}
        rows={filtered}
        defaultSort={{
          col: "ticker",
          dir: "asc",
        }}
        onDownload={(r) =>
          downloadCsv(r, visibleCsvCols, "screener")
        }
      />
    </div>
  );
}

function TargetsTab() {
  const data = useTargets();
  const [market, setMarket] = useState("all");
  const [sector, setSector] = useState("all");
  const [ticker, setTicker] = useState("all");

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    return applyFilters(
      data.value.rows,
      market,
      sector,
      ticker,
    );
  }, [data.value, market, sector, ticker]);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return <WidgetError message={data.error} data-testid="insights-error" />;

  return (
    <div className="space-y-4">
      <InsightsFilters
        market={market}
        onMarketChange={setMarket}
        sector={sector}
        onSectorChange={setSector}
        sectors={data.value?.sectors ?? []}
        ticker={ticker}
        onTickerChange={setTicker}
        tickers={data.value?.tickers ?? []}
      />
      <InsightsTable<TargetRow>
        columns={targetCols}
        rows={filtered}
        defaultSort={{
          col: "run_date",
          dir: "desc",
        }}
        onDownload={(r) =>
          downloadCsv(
            r, targetCsvCols, "price-targets",
          )
        }
      />
    </div>
  );
}

function DividendsTab() {
  const data = useDividends();
  const [market, setMarket] = useState("all");
  const [sector, setSector] = useState("all");
  const [ticker, setTicker] = useState("all");

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    return applyFilters(
      data.value.rows,
      market,
      sector,
      ticker,
    );
  }, [data.value, market, sector, ticker]);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return <WidgetError message={data.error} data-testid="insights-error" />;

  return (
    <div className="space-y-4">
      <InsightsFilters
        market={market}
        onMarketChange={setMarket}
        sector={sector}
        onSectorChange={setSector}
        sectors={data.value?.sectors ?? []}
        ticker={ticker}
        onTickerChange={setTicker}
        tickers={data.value?.tickers ?? []}
      />
      <InsightsTable<DividendRow>
        columns={dividendCols}
        rows={filtered}
        defaultSort={{
          col: "ex_date",
          dir: "desc",
        }}
        onDownload={(r) =>
          downloadCsv(
            r, dividendCsvCols, "dividends",
          )
        }
      />
    </div>
  );
}

function RiskTab() {
  const data = useRisk();
  const [market, setMarket] = useState("all");
  const [sector, setSector] = useState("all");

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    return applyFilters(
      data.value.rows,
      market,
      sector,
      "all",
    );
  }, [data.value, market, sector]);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return <WidgetError message={data.error} data-testid="insights-error" />;

  return (
    <div className="space-y-4">
      <InsightsFilters
        market={market}
        onMarketChange={setMarket}
        sector={sector}
        onSectorChange={setSector}
        sectors={data.value?.sectors ?? []}
      />
      <InsightsTable<RiskRow>
        columns={riskCols}
        rows={filtered}
        defaultSort={{
          col: "sharpe_ratio",
          dir: "desc",
        }}
        onDownload={(r) =>
          downloadCsv(r, riskCsvCols, "risk-metrics")
        }
      />
    </div>
  );
}

function SectorsTab() {
  const [market, setMarket] = useState("all");
  const data = useSectors(market);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return <WidgetError message={data.error} data-testid="insights-error" />;

  const rows = data.value?.rows ?? [];

  // Bar chart data.
  const chartData: Plotly.Data[] = [
    {
      type: "bar",
      x: rows.map((r) => r.sector),
      y: rows.map((r) => r.avg_return_pct ?? 0),
      marker: {
        color: rows.map((r) =>
          (r.avg_return_pct ?? 0) >= 0
            ? "#10b981"
            : "#ef4444",
        ),
      },
      text: rows.map((r) =>
        r.avg_return_pct != null
          ? `${r.avg_return_pct.toFixed(1)}%`
          : "",
      ),
      textposition: "outside",
    },
  ];

  return (
    <div className="space-y-4">
      <InsightsFilters
        market={market}
        onMarketChange={setMarket}
      />
      {rows.length > 0 && (
        <div data-testid="insights-chart">
        <PlotlyChart
          data={chartData}
          layout={{
            title: {
              text: "Average Annualized Return by Sector",
              font: { size: 14 },
            },
            showlegend: false,
            yaxis: { title: { text: "Avg Return %" } },
            xaxis: {
              tickangle: -30,
              automargin: true,
            },
          }}
          height={320}
        />
        </div>
      )}
      <InsightsTable<SectorRow>
        columns={sectorCols}
        rows={rows}
        defaultSort={{
          col: "avg_return_pct",
          dir: "desc",
        }}
        onDownload={(r) =>
          downloadCsv(r, sectorCsvCols, "sectors")
        }
      />
    </div>
  );
}

function CorrelationTab() {
  const [period, setPeriod] = useState("1y");
  const data = useCorrelation(period, "all");

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return (
      <WidgetError
        message={data.error}
        data-testid="insights-error"
      />
    );

  const tickers = data.value?.tickers ?? [];
  const matrix = data.value?.matrix ?? [];

  const periodLabel = period === "1y"
    ? "1 Year"
    : period === "3y"
      ? "3 Years"
      : "All Time";

  return (
    <div className="space-y-4">
      {/* Period filter */}
      <div className="flex items-center gap-3">
        <div
          className="flex gap-0.5 rounded-[10px]
            border border-gray-200 bg-gray-50
            p-[3px] dark:border-gray-700
            dark:bg-gray-800"
        >
          {(
            [
              { key: "1y", label: "1 Year" },
              { key: "3y", label: "3 Years" },
              { key: "all", label: "All Time" },
            ] as const
          ).map((p) => (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              className={`
                rounded-lg px-3.5 py-[7px]
                text-[13px] font-semibold
                transition-all
                ${
                  period === p.key
                    ? "bg-white text-indigo-600 shadow-sm dark:bg-gray-700 dark:text-indigo-400"
                    : "text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                }
              `}
            >
              {p.label}
            </button>
          ))}
        </div>
        <span
          className="font-mono text-xs text-gray-400
            dark:text-gray-500"
        >
          {tickers.length} portfolio stock
          {tickers.length !== 1 ? "s" : ""}
        </span>
      </div>

      {tickers.length >= 2 ? (
        <div
          data-testid="insights-chart"
          className="rounded-2xl border border-gray-200
            bg-white p-4 dark:border-gray-800
            dark:bg-gray-900/80"
        >
          <CorrelationHeatmap
            tickers={tickers}
            matrix={matrix}
            title={`Portfolio Correlation — Daily Returns (${periodLabel})`}
          />
        </div>
      ) : (
        <div
          data-testid="insights-empty"
          className="rounded-2xl border border-gray-200
            bg-white p-12 text-center
            dark:border-gray-800 dark:bg-gray-900/80"
        >
          <div
            className="mx-auto mb-4 flex h-14 w-14
              items-center justify-center rounded-full
              bg-indigo-50 text-indigo-500
              dark:bg-indigo-500/12
              dark:text-indigo-400"
          >
            <svg
              viewBox="0 0 24 24"
              className="h-6 w-6"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect
                x="3" y="3"
                width="18" height="18"
                rx="2"
              />
              <path d="M3 9h18M9 21V9" />
            </svg>
          </div>
          <p
            className="text-sm font-semibold
              text-gray-700 dark:text-gray-300"
          >
            Not enough portfolio stocks
          </p>
          <p
            className="mt-1 text-xs text-gray-400
              dark:text-gray-500"
          >
            Add at least 2 stocks to your portfolio
            to see correlation analysis.
          </p>
        </div>
      )}
    </div>
  );
}

function QuarterlyTab() {
  const [stmtType, setStmtType] =
    useState("income");
  const [market, setMarket] = useState("all");
  const [sector, setSector] =
    useState("portfolio");
  const [ticker, setTicker] = useState("all");
  const data = useQuarterly(stmtType);
  const portfolioData = usePortfolio();

  const portfolioTickerSet = useMemo(
    () =>
      new Set(
        portfolioData.holdings.map(
          (h) => h.ticker,
        ),
      ),
    [portfolioData.holdings],
  );

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    let rows = data.value.rows;
    // Portfolio filter — applied before other
    // filters so it acts like a sector
    if (sector === "portfolio") {
      rows = rows.filter((r) =>
        portfolioTickerSet.has(r.ticker),
      );
    }
    return applyFilters(
      rows,
      market,
      sector === "portfolio" ? "all" : sector,
      ticker,
    );
  }, [
    data.value,
    market,
    sector,
    ticker,
    portfolioTickerSet,
  ]);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return <WidgetError message={data.error} data-testid="insights-error" />;

  // Dynamic columns based on statement type.
  const baseCols: Column<QuarterlyRow>[] = [
    { key: "ticker", label: "Ticker" },
    {
      key: "quarter_label",
      label: "Quarter",
      render: (r) => r.quarter_label ?? "\u2014",
    },
  ];

  let metricCols: Column<QuarterlyRow>[] = [];
  if (stmtType === "income") {
    metricCols = [
      {
        key: "revenue",
        label: "Revenue",
        numeric: true,
        render: (r) => fmtNum(r.revenue, 0),
      },
      {
        key: "net_income",
        label: "Net Income",
        numeric: true,
        render: (r) => (
          <span
            className={pctColor(r.net_income)}
          >
            {fmtNum(r.net_income, 0)}
          </span>
        ),
      },
      {
        key: "eps",
        label: "EPS",
        numeric: true,
        render: (r) => fmtNum(r.eps),
      },
    ];
  } else if (stmtType === "balance") {
    metricCols = [
      {
        key: "total_assets",
        label: "Total Assets",
        numeric: true,
        render: (r) => fmtNum(r.total_assets, 0),
      },
      {
        key: "total_equity",
        label: "Equity",
        numeric: true,
        render: (r) => fmtNum(r.total_equity, 0),
      },
    ];
  } else {
    metricCols = [
      {
        key: "operating_cashflow",
        label: "Op. CF",
        numeric: true,
        render: (r) => (
          <span
            className={pctColor(
              r.operating_cashflow,
            )}
          >
            {fmtNum(r.operating_cashflow, 0)}
          </span>
        ),
      },
      {
        key: "free_cashflow",
        label: "Free CF",
        numeric: true,
        render: (r) => (
          <span
            className={pctColor(r.free_cashflow)}
          >
            {fmtNum(r.free_cashflow, 0)}
          </span>
        ),
      },
    ];
  }

  const allCols = [...baseCols, ...metricCols];

  // Chart: first metric pair for filtered rows.
  let chartData: Plotly.Data[] = [];
  if (filtered.length > 0) {
    const labels = filtered.map(
      (r) =>
        `${r.ticker} ${r.quarter_label ?? ""}`.trim(),
    );
    if (stmtType === "income") {
      chartData = [
        {
          type: "bar",
          name: "Revenue",
          x: labels,
          y: filtered.map((r) => r.revenue ?? 0),
          marker: { color: "#6366f1" },
        },
        {
          type: "bar",
          name: "Net Income",
          x: labels,
          y: filtered.map(
            (r) => r.net_income ?? 0,
          ),
          marker: { color: "#10b981" },
        },
      ];
    } else if (stmtType === "balance") {
      chartData = [
        {
          type: "bar",
          name: "Total Assets",
          x: labels,
          y: filtered.map(
            (r) => r.total_assets ?? 0,
          ),
          marker: { color: "#6366f1" },
        },
        {
          type: "bar",
          name: "Equity",
          x: labels,
          y: filtered.map(
            (r) => r.total_equity ?? 0,
          ),
          marker: { color: "#10b981" },
        },
      ];
    } else {
      chartData = [
        {
          type: "bar",
          name: "Operating CF",
          x: labels,
          y: filtered.map(
            (r) => r.operating_cashflow ?? 0,
          ),
          marker: { color: "#6366f1" },
        },
        {
          type: "bar",
          name: "Free CF",
          x: labels,
          y: filtered.map(
            (r) => r.free_cashflow ?? 0,
          ),
          marker: { color: "#10b981" },
        },
      ];
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select
          data-testid="insights-statement-type"
          value={stmtType}
          onChange={(e) =>
            setStmtType(e.target.value)
          }
          className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2.5 py-1.5 text-sm text-gray-700 dark:text-gray-200"
        >
          <option value="income">Income</option>
          <option value="balance">Balance</option>
          <option value="cashflow">Cashflow</option>
        </select>
        <InsightsFilters
          market={market}
          onMarketChange={setMarket}
          sector={sector}
          onSectorChange={setSector}
          sectors={[
            "portfolio",
            ...(data.value?.sectors ?? []),
          ]}
          ticker={ticker}
          onTickerChange={setTicker}
          tickers={data.value?.tickers ?? []}
        />
      </div>

      {chartData.length > 0 && (
        <div data-testid="insights-chart">
        <PlotlyChart
          data={chartData}
          layout={{
            title: {
              text: "Quarter-over-Quarter Results",
              font: { size: 14 },
            },
            barmode: "group",
            xaxis: {
              tickangle: -30,
              automargin: true,
            },
          }}
          height={360}
        />
        </div>
      )}

      <InsightsTable<QuarterlyRow>
        columns={allCols}
        rows={filtered}
        defaultSort={{
          col: "quarter_end",
          dir: "desc",
        }}
        onDownload={(r) => {
          const csv = allCols.map((c) => ({
            key: c.key,
            header: c.label,
          })) as CsvColumn<QuarterlyRow>[];
          downloadCsv(
            r,
            csv,
            `quarterly-${stmtType}`,
          );
        }}
      />
    </div>
  );
}

function PiotroskiTab() {
  const [sector, setSector] = useState("all");
  const [minScore, setMinScore] = useState(0);
  const [market, setMarket] = useState("all");
  const data = usePiotroski(minScore, sector, market);

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    return data.value.rows;
  }, [data.value]);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return (
      <WidgetError
        message={data.error}
        data-testid="insights-error"
      />
    );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {/* Sector filter */}
        {(data.value?.sectors ?? []).length > 0 && (
          <select
            data-testid="piotroski-sector-filter"
            value={sector}
            onChange={(e) =>
              setSector(e.target.value)
            }
            className="rounded-lg border border-gray-300
              dark:border-gray-600 bg-white dark:bg-gray-800
              px-2.5 py-1.5 text-sm
              text-gray-700 dark:text-gray-200
              focus:outline-none focus:ring-2
              focus:ring-indigo-500/40"
          >
            <option value="all">All Sectors</option>
            {(data.value?.sectors ?? []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}
        {/* Market filter */}
        <select
          data-testid="piotroski-market-filter"
          value={market}
          onChange={(e) =>
            setMarket(e.target.value)
          }
          className="rounded-lg border border-gray-300
            dark:border-gray-600 bg-white dark:bg-gray-800
            px-2.5 py-1.5 text-sm
            text-gray-700 dark:text-gray-200
            focus:outline-none focus:ring-2
            focus:ring-indigo-500/40"
        >
          <option value="all">All Markets</option>
          <option value="india">India</option>
          <option value="us">US</option>
        </select>
        {/* Min score filter */}
        <select
          data-testid="piotroski-score-filter"
          value={minScore}
          onChange={(e) =>
            setMinScore(Number(e.target.value))
          }
          className="rounded-lg border border-gray-300
            dark:border-gray-600 bg-white dark:bg-gray-800
            px-2.5 py-1.5 text-sm
            text-gray-700 dark:text-gray-200
            focus:outline-none focus:ring-2
            focus:ring-indigo-500/40"
        >
          <option value={0}>All Scores</option>
          <option value={8}>Strong (8-9)</option>
          <option value={5}>Moderate+ (5-9)</option>
        </select>
        {data.value?.score_date && (
          <span className="text-xs text-gray-400 dark:text-gray-500 ml-auto">
            Scored: {data.value.score_date}
          </span>
        )}
      </div>
      <InsightsTable<PiotroskiRow>
        columns={piotroskiCols}
        rows={filtered}
        defaultSort={{
          col: "total_score",
          dir: "desc",
        }}
        onDownload={(r) =>
          downloadCsv(
            r,
            piotroskiCsvCols,
            "piotroski-fscore",
          )
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------
// ScreenQL — universal stock screener
// ---------------------------------------------------------------

const SCREENQL_PRESETS = [
  {
    label: "Value Picks",
    query:
      "pe_ratio < 15\n" +
      "price_to_book < 3\n" +
      "dividend_yield > 2",
  },
  {
    label: "Growth Stars",
    query:
      "earnings_growth > 20\n" +
      "revenue_growth > 20\n" +
      "sharpe_ratio > 0.5",
  },
  {
    label: "Quality + Momentum",
    query:
      "piotroski_score >= 7\n" +
      "annualized_return_pct > 15\n" +
      "rsi_14 < 70",
  },
  {
    label: "Undervalued Large Caps",
    query:
      "market_cap > 50000\n" +
      "pe_ratio < 20\n" +
      "sentiment_score > 0.2",
  },
  {
    label: "High Conviction Forecasts",
    query:
      "forecast_confidence > 0.6\n" +
      "target_6m_pct > 10",
  },
  {
    label: "Dividend Champions",
    query:
      "dividend_yield > 3\n" +
      "piotroski_score >= 5\n" +
      "profit_margins > 10",
  },
];

interface ScreenField {
  name: string;
  label: string;
  type: string;
  category: string;
}

function ScreenQLTab() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [query, setQuery] = useState(
    searchParams.get("q") ?? "",
  );
  const [results, setResults] = useState<{
    rows: Record<string, unknown>[];
    total: number;
    page: number;
    page_size: number;
    columns_used: string[];
    excluded_null_count: number;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [fields, setFields] = useState<
    ScreenField[]
  >([]);
  // Column picker — selected keys are extra display
  // columns passed to `/screen` as `display_columns`.
  // Catalog is /screen/fields (37-field set).
  const screenqlCatalog = useMemo(
    () =>
      fields.map((f) => ({
        key: f.name,
        label: f.label,
        category: f.category,
      })),
    [fields],
  );
  const screenqlAllKeys = useMemo(
    () => fields.map((f) => f.name),
    [fields],
  );
  const [
    selectedScreenqlCols,
    setSelectedScreenqlCols,
    resetScreenqlCols,
  ] = useColumnSelection(
    "insights.columns.screenql",
    // Default: no extras — the filter-referenced
    // fields + base columns render by themselves.
    [],
    screenqlAllKeys,
  );
  const [suggestions, setSuggestions] = useState<
    ScreenField[]
  >([]);
  const [showSuggestions, setShowSuggestions] =
    useState(false);
  const textareaRef =
    useRef<HTMLTextAreaElement>(null);

  // Fetch field catalog once
  useEffect(() => {
    const url = `${
      process.env.NEXT_PUBLIC_BACKEND_URL ??
      "http://localhost:8181"
    }/v1/insights/screen/fields`;
    fetch(url)
      .then((r) => r.json())
      .then((d) => setFields(d.fields ?? []))
      .catch(() => {});
  }, []);

  // Auto-run if URL has ?q= param
  useEffect(() => {
    const q = searchParams.get("q");
    if (q && !results && !loading) {
      setQuery(q);
      runScreen(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runScreen = useCallback(
    async (q?: string) => {
      const text = q ?? query;
      if (!text.trim()) return;
      setLoading(true);
      setError("");
      try {
        const { apiFetch } = await import(
          "@/lib/apiFetch"
        );
        const API_URL = `${
          process.env
            .NEXT_PUBLIC_BACKEND_URL ??
          "http://localhost:8181"
        }/v1`;
        const res = await apiFetch(
          `${API_URL}/insights/screen`,
          {
            method: "POST",
            headers: {
              "Content-Type":
                "application/json",
            },
            body: JSON.stringify({
              query: text,
              page: 1,
              page_size: 25,
              sort_by: null,
              sort_dir: "desc",
              display_columns: selectedScreenqlCols,
            }),
          },
        );
        if (!res.ok) {
          const err = await res.json();
          setError(
            err.detail ?? "Query failed",
          );
          setResults(null);
        } else {
          const data = await res.json();
          setResults(data);
          // Update URL
          router.replace(
            `/analytics/insights?tab=screenql&q=${encodeURIComponent(text)}`,
            { scroll: false },
          );
        }
      } catch (e) {
        setError(
          e instanceof Error
            ? e.message
            : "Query failed",
        );
        setResults(null);
      }
      setLoading(false);
    },
    [query, router, selectedScreenqlCols],
  );

  // Re-run the current query when the user toggles a
  // column so new fields appear/disappear in results.
  // Only fires if a query has already been executed.
  useEffect(() => {
    if (!results) return;
    runScreen(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedScreenqlCols]);

  // Autocomplete logic
  const handleQueryChange = useCallback(
    (val: string) => {
      setQuery(val);
      // Get current word at cursor
      if (!textareaRef.current) {
        setSuggestions([]);
        return;
      }
      const pos =
        textareaRef.current.selectionStart;
      const before = val.slice(0, pos);
      const match = before.match(
        /([a-zA-Z_]\w*)$/,
      );
      if (
        match &&
        match[1].length >= 2 &&
        fields.length > 0
      ) {
        const partial = match[1].toLowerCase();
        const matches = fields.filter((f) =>
          f.name
            .toLowerCase()
            .includes(partial),
        );
        setSuggestions(matches.slice(0, 8));
        setShowSuggestions(matches.length > 0);
      } else {
        setSuggestions([]);
        setShowSuggestions(false);
      }
    },
    [fields],
  );

  const acceptSuggestion = useCallback(
    (fieldName: string) => {
      if (!textareaRef.current) return;
      const pos =
        textareaRef.current.selectionStart;
      const before = query.slice(0, pos);
      const after = query.slice(pos);
      const match = before.match(
        /([a-zA-Z_]\w*)$/,
      );
      if (match) {
        const start =
          before.length - match[1].length;
        const newVal =
          before.slice(0, start) +
          fieldName +
          " " +
          after;
        setQuery(newVal);
      }
      setShowSuggestions(false);
      textareaRef.current.focus();
    },
    [query],
  );

  // Build dynamic columns for results
  const dynamicCols = useMemo(() => {
    if (!results) return [];

    const SYM: Record<string, string> = {
      INR: "\u20B9",
      USD: "$",
      GBP: "\u00A3",
      EUR: "\u20AC",
    };
    const sym = (
      r: Record<string, unknown>,
    ) => {
      const c = String(r.currency ?? "");
      return SYM[c] ?? c;
    };

    const baseCols: Column<
      Record<string, unknown>
    >[] = [
      { key: "ticker", label: "Ticker" },
      {
        key: "company_name",
        label: "Company",
      },
      { key: "sector", label: "Sector" },
      {
        key: "market_cap",
        label: "MCap (Cr)",
        numeric: true,
        render: (r) =>
          r.market_cap != null
            ? `${sym(r)}${Number(
                Number(r.market_cap) / 1e7,
              ).toLocaleString("en-IN", {
                maximumFractionDigits: 0,
              })}`
            : "\u2014",
      },
      {
        key: "current_price",
        label: "Price",
        numeric: true,
        render: (r) =>
          r.current_price != null
            ? `${sym(r)}${Number(
                r.current_price,
              ).toFixed(2)}`
            : "\u2014",
      },
    ];

    // Hidden helper cols (not displayed)
    const hiddenKeys = new Set([
      "currency", "market",
    ]);

    const baseKeys = new Set(
      baseCols.map((c) => c.key),
    );
    const extra: Column<
      Record<string, unknown>
    >[] = [];
    for (const col of results.columns_used) {
      if (
        baseKeys.has(col) ||
        hiddenKeys.has(col)
      ) {
        continue;
      }
      extra.push({
        key: col,
        label: col
          .replace(/_/g, " ")
          .replace(
            /\b\w/g,
            (c) => c.toUpperCase(),
          ),
        numeric: ![
          "sector", "industry",
          "market", "currency",
          "rsi_signal", "macd_signal",
          "sma_200_signal",
          "piotroski_label",
        ].includes(col),
        render: (r) => {
          const v = r[col];
          if (v == null) return "\u2014";
          if (typeof v === "number") {
            return v.toFixed(2);
          }
          return String(v);
        },
      });
    }

    const actionCol: Column<
      Record<string, unknown>
    > = {
      key: "action" as keyof Record<
        string,
        unknown
      > &
        string,
      label: "Action",
      sortable: false,
      render: (r) => (
        <button
          title="Stock Analysis"
          onClick={() =>
            window.open(
              `/analytics/analysis?ticker=${encodeURIComponent(String(r.ticker))}&tab=analysis`,
              "_blank",
            )
          }
          className="flex h-7 w-7
            items-center justify-center
            rounded-md border
            border-gray-200 text-gray-400
            transition-all
            hover:border-indigo-400
            hover:bg-indigo-50
            hover:text-indigo-600
            dark:border-gray-700
            dark:text-gray-500
            dark:hover:border-indigo-500
            dark:hover:bg-indigo-500/10
            dark:hover:text-indigo-400"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-3.5 w-3.5"
          >
            <path d="M15.5 2A1.5 1.5 0 0014 3.5v13a1.5 1.5 0 001.5 1.5h1a1.5 1.5 0 001.5-1.5v-13A1.5 1.5 0 0016.5 2h-1zM9.5 6A1.5 1.5 0 008 7.5v9A1.5 1.5 0 009.5 18h1a1.5 1.5 0 001.5-1.5v-9A1.5 1.5 0 0010.5 6h-1zM3.5 10A1.5 1.5 0 002 11.5v5A1.5 1.5 0 003.5 18h1A1.5 1.5 0 006 16.5v-5A1.5 1.5 0 004.5 10h-1z" />
          </svg>
        </button>
      ),
    };

    return [
      ...baseCols, ...extra, actionCol,
    ];
  }, [results]);

  // CSV columns
  const csvCols = useMemo(() => {
    if (!dynamicCols.length) return [];
    return dynamicCols.map((c) => ({
      key: c.key,
      header: c.label,
    })) as CsvColumn<
      Record<string, unknown>
    >[];
  }, [dynamicCols]);

  return (
    <div className="space-y-4">
      {/* Preset chips */}
      <div
        className="flex flex-wrap gap-2"
        data-testid="screenql-presets"
      >
        {SCREENQL_PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => {
              setQuery(p.query);
              setShowSuggestions(false);
            }}
            className="px-3 py-1 text-xs
              font-medium rounded-full border
              border-gray-300 dark:border-gray-600
              text-gray-600 dark:text-gray-300
              hover:bg-indigo-50
              dark:hover:bg-indigo-900/20
              hover:border-indigo-400
              dark:hover:border-indigo-500
              hover:text-indigo-600
              dark:hover:text-indigo-400
              transition-colors"
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Query input + run */}
      <div className="relative">
        <textarea
          ref={textareaRef}
          data-testid="screenql-input"
          value={query}
          onChange={(e) =>
            handleQueryChange(e.target.value)
          }
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              (e.metaKey || e.ctrlKey)
            ) {
              e.preventDefault();
              runScreen();
            }
            if (e.key === "Escape") {
              setShowSuggestions(false);
            }
          }}
          onBlur={() =>
            setTimeout(
              () => setShowSuggestions(false),
              150,
            )
          }
          placeholder={
            "Type conditions... e.g. " +
            "pe_ratio < 15 AND market_cap > 50000"
          }
          rows={4}
          className="w-full rounded-lg border
            border-gray-300 dark:border-gray-600
            bg-white dark:bg-gray-800
            px-3 py-2 text-sm font-mono
            text-gray-700 dark:text-gray-200
            placeholder:text-gray-400
            focus:outline-none focus:ring-2
            focus:ring-indigo-500/40
            resize-y"
        />

        {/* Autocomplete dropdown */}
        {showSuggestions &&
          suggestions.length > 0 && (
          <div
            className="absolute z-20 mt-1
            w-72 rounded-lg border
            border-gray-200 dark:border-gray-700
            bg-white dark:bg-gray-800
            shadow-lg overflow-hidden"
          >
            {suggestions.map((s) => (
              <button
                key={s.name}
                onMouseDown={(e) => {
                  e.preventDefault();
                  acceptSuggestion(s.name);
                }}
                className="w-full px-3 py-1.5
                  text-left text-xs
                  hover:bg-indigo-50
                  dark:hover:bg-indigo-900/20
                  flex justify-between
                  items-center"
              >
                <span className="font-mono
                  text-gray-700
                  dark:text-gray-200">
                  {s.name}
                </span>
                <span className="text-gray-400
                  dark:text-gray-500">
                  {s.type} &middot; {s.category}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Run button + info */}
      <div className="flex items-center gap-3">
        <button
          data-testid="screenql-run"
          onClick={() => runScreen()}
          disabled={loading || !query.trim()}
          className="px-4 py-1.5 text-sm
            font-medium text-white
            bg-indigo-600 rounded-lg
            hover:bg-indigo-700
            disabled:opacity-50
            disabled:cursor-not-allowed
            transition-colors"
        >
          {loading
            ? "Running\u2026"
            : "Run Screen"}
        </button>
        <span className="text-xs text-gray-400">
          {typeof navigator !== "undefined" &&
          /Mac/i.test(navigator.platform)
            ? "⌘+Enter"
            : "Ctrl+Enter"}{" "}
          to run
        </span>
        {results && (
          <span
            className="text-xs font-medium
            text-indigo-600 dark:text-indigo-400"
          >
            {results.total} results
            {results.excluded_null_count > 0 &&
              ` (${results.excluded_null_count} excluded)`}
          </span>
        )}
      </div>

      {/* Error banner */}
      {error && (
        <div
          data-testid="screenql-error"
          className="rounded-lg border
          border-red-200 dark:border-red-800
          bg-red-50 dark:bg-red-900/20
          px-4 py-2 text-sm text-red-700
          dark:text-red-400 flex
          items-start gap-2"
        >
          <span className="shrink-0 mt-0.5">
            &#9888;
          </span>
          <span>{error}</span>
          <button
            onClick={() => setError("")}
            className="ml-auto shrink-0
              text-red-400 hover:text-red-600"
          >
            &times;
          </button>
        </div>
      )}

      {/* Column selector + results table */}
      {results && dynamicCols.length > 0 && (
        <>
          <div className="flex justify-end">
            <ColumnSelector
              catalog={screenqlCatalog}
              selected={selectedScreenqlCols}
              onChange={setSelectedScreenqlCols}
              onReset={resetScreenqlCols}
              buttonLabel="Extra columns"
            />
          </div>
          <InsightsTable<
            Record<string, unknown>
          >
            columns={dynamicCols}
            rows={
              results.rows as Record<
                string,
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                any
              >[]
            }
            defaultSort={{
              col: "market_cap",
              dir: "desc",
            }}
            onDownload={(r) =>
              downloadCsv(
                r, csvCols, "screenql-results",
              )
            }
          />
        </>
      )}

      {results &&
        results.rows.length === 0 &&
        !loading && (
        <div
          className="py-12 text-center
            text-gray-400 dark:text-gray-500"
        >
          No matching stocks found.
          Try adjusting your conditions.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Main page
// ---------------------------------------------------------------

function InsightsPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [activeTab, setActiveTab] =
    useState<TabId>(
      (searchParams.get("tab") as TabId) ??
        "screener",
    );

  const handleTabChange = useCallback(
    (id: TabId) => {
      setActiveTab(id);
      router.replace(
        `/analytics/insights?tab=${id}`,
        { scroll: false },
      );
    },
    [router],
  );

  const renderTab = useCallback(() => {
    switch (activeTab) {
      case "screener":
        return <ScreenerTab />;
      case "targets":
        return <TargetsTab />;
      case "dividends":
        return <DividendsTab />;
      case "risk":
        return <RiskTab />;
      case "sectors":
        return <SectorsTab />;
      case "correlation":
        return <CorrelationTab />;
      case "quarterly":
        return <QuarterlyTab />;
      case "piotroski":
        return <PiotroskiTab />;
      case "screenql":
        return <ScreenQLTab />;
    }
  }, [activeTab]);

  return (
    <div className="space-y-6 p-4 sm:p-6">
      {/* Tab bar */}
      <div className="flex gap-1 overflow-x-auto border-b border-gray-200 dark:border-gray-700 pb-px">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            data-testid={`insights-tab-${tab.id}`}
            onClick={() => handleTabChange(tab.id)}
            className={`
              whitespace-nowrap px-3 py-2 text-sm
              font-medium rounded-t-lg transition-colors
              ${
                activeTab === tab.id
                  ? "text-indigo-600 dark:text-indigo-400 border-b-2 border-indigo-600 dark:border-indigo-400 -mb-px"
                  : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
              }
            `}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="min-h-[400px]">
        {renderTab()}
      </div>
    </div>
  );
}

export default function InsightsPage() {
  return (
    <Suspense fallback={null}>
      <InsightsPageInner />
    </Suspense>
  );
}
