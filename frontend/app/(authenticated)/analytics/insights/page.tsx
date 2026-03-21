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
  { id: "targets", label: "Price Targets" },
  { id: "dividends", label: "Dividends" },
  { id: "risk", label: "Risk Metrics" },
  { id: "sectors", label: "Sectors" },
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
  const [market, setMarket] = useState("all");
  const data = useCorrelation(period, market);

  if (data.loading) return <WidgetSkeleton />;
  if (data.error)
    return <WidgetError message={data.error} data-testid="insights-error" />;

  const tickers = data.value?.tickers ?? [];
  const matrix = data.value?.matrix ?? [];

  const chartData: Plotly.Data[] = [
    {
      type: "heatmap",
      z: matrix,
      x: tickers,
      y: tickers,
      texttemplate: "%{z:.2f}",
      colorscale: "RdBu",
      zmin: -1,
      zmax: 1,
      reversescale: true,
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select
          data-testid="insights-period-filter"
          value={period}
          onChange={(e) =>
            setPeriod(e.target.value)
          }
          className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2.5 py-1.5 text-sm text-gray-700 dark:text-gray-200"
        >
          <option value="1y">1 Year</option>
          <option value="3y">3 Years</option>
          <option value="all">All Time</option>
        </select>
        <InsightsFilters
          market={market}
          onMarketChange={setMarket}
        />
      </div>

      {tickers.length >= 2 ? (
        <div data-testid="insights-chart">
        <PlotlyChart
          data={chartData}
          layout={{
            title: {
              text: `Daily Returns Correlation (${period.toUpperCase()})`,
              font: { size: 14 },
            },
            xaxis: { tickangle: -45 },
          }}
          height={Math.max(
            400,
            tickers.length * 40 + 100,
          )}
        />
        </div>
      ) : (
        <div
          data-testid="insights-empty"
          className="py-12 text-center text-gray-400"
        >
          Need at least 2 tickers with data for
          correlation
        </div>
      )}
    </div>
  );
}

function QuarterlyTab() {
  const [stmtType, setStmtType] =
    useState("income");
  const [market, setMarket] = useState("all");
  const [sector, setSector] = useState("all");
  const [ticker, setTicker] = useState("all");
  const data = useQuarterly(stmtType);

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
          sectors={data.value?.sectors ?? []}
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
