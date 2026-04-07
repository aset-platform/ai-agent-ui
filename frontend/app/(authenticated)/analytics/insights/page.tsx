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
} from "react";
import {
  useScreener,
  useTargets,
  useDividends,
  useRisk,
  useSectors,
  useCorrelation,
  useQuarterly,
} from "@/hooks/useInsightsData";
import {
  InsightsTable,
  type Column,
} from "@/components/insights/InsightsTable";
import { InsightsFilters } from "@/components/insights/InsightsFilters";
import { PlotlyChart } from "@/components/charts/PlotlyChart";
import { CorrelationHeatmap } from "@/components/charts/CorrelationHeatmap";
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
  | "quarterly";

const TABS: { id: TabId; label: string }[] = [
  { id: "screener", label: "Screener" },
  { id: "risk", label: "Risk Metrics" },
  { id: "sectors", label: "Sectors" },
  { id: "targets", label: "Price Targets" },
  { id: "dividends", label: "Dividends" },
  { id: "correlation", label: "Correlation" },
  { id: "quarterly", label: "Quarterly" },
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

// ---------------------------------------------------------------
// Tab content components
// ---------------------------------------------------------------

function ScreenerTab() {
  const data = useScreener();
  const [market, setMarket] = useState("all");
  const [sector, setSector] = useState("all");
  const [rsiFilter, setRsiFilter] = useState("all");

  const filtered = useMemo(() => {
    if (!data.value?.rows) return [];
    return applyFilters(
      data.value.rows,
      market,
      sector,
      "all",
      rsiFilter,
    );
  }, [data.value, market, sector, rsiFilter]);

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
        rsiFilter={rsiFilter}
        onRsiFilterChange={setRsiFilter}
      />
      <InsightsTable<ScreenerRow>
        columns={screenerCols}
        rows={filtered}
        defaultSort={{
          col: "ticker",
          dir: "asc",
        }}
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
      />
    </div>
  );
}

function CorrelationTab() {
  const [period, setPeriod] = useState("1y");
  const data = useCorrelation(
    period, "all", "portfolio",
  );

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
      />
    </div>
  );
}

// ---------------------------------------------------------------
// Main page
// ---------------------------------------------------------------

export default function InsightsPage() {
  const [activeTab, setActiveTab] =
    useState<TabId>("screener");

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
            onClick={() => setActiveTab(tab.id)}
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
