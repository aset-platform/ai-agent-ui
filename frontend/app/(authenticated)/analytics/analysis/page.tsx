"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { CompareContent } from "../compare/page";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import {
  PlotlyChart,
  CHART_COLORS,
} from "@/components/charts/PlotlyChart";
import {
  buildForecastChart,
} from "@/components/charts/chartBuilders";
import type {
  OHLCVResponse,
  IndicatorsResponse,
  ForecastSeriesResponse,
  ForecastsResponse,
  TickerForecast,
} from "@/lib/types";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

type TabId = "analysis" | "forecast" | "compare";

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

/** Currency symbol based on ticker suffix. */
function tickerCurrency(ticker: string): string {
  if (ticker.endsWith(".NS") || ticker.endsWith(".BO"))
    return "₹";
  return "$";
}

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div
      className="
        rounded-lg p-4
        bg-gray-50 dark:bg-gray-800/50
        border border-gray-100 dark:border-gray-700/50
      "
    >
      <p
        className="
          text-xs font-medium uppercase tracking-wider
          text-gray-400 dark:text-gray-500 mb-1
        "
      >
        {label}
      </p>
      <p
        className={`
          font-mono text-xl font-semibold
          ${color ?? "text-gray-900 dark:text-gray-100"}
        `}
      >
        {value}
      </p>
      {sub && (
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
          {sub}
        </p>
      )}
    </div>
  );
}

function ChartSkeleton({ h = "h-64" }: { h?: string }) {
  return (
    <div
      className={`
        flex items-center justify-center ${h}
        bg-gray-100 dark:bg-gray-800
        rounded-lg animate-pulse
      `}
    >
      <span className="text-sm text-gray-400">
        Loading chart...
      </span>
    </div>
  );
}

// ---------------------------------------------------------------
// Tab: Analysis
// ---------------------------------------------------------------

function AnalysisTab({ ticker }: { ticker: string }) {
  const [ohlcv, setOhlcv] =
    useState<OHLCVResponse | null>(null);
  const [indicators, setIndicators] =
    useState<IndicatorsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const q = encodeURIComponent(ticker);
    Promise.all([
      apiFetch(
        `${API_URL}/dashboard/chart/ohlcv?ticker=${q}`,
      ).then((r) => {
        if (!r.ok) throw new Error(`OHLCV: HTTP ${r.status}`);
        return r.json() as Promise<OHLCVResponse>;
      }),
      apiFetch(
        `${API_URL}/dashboard/chart/indicators?ticker=${q}`,
      ).then((r) => {
        if (!r.ok) {
          throw new Error(`Indicators: HTTP ${r.status}`);
        }
        return r.json() as Promise<IndicatorsResponse>;
      }),
    ])
      .then(([o, ind]) => {
        if (cancelled) return;
        setOhlcv(o);
        setIndicators(ind);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  const sym = tickerCurrency(ticker);

  // --- Price chart traces (candlestick + volume + SMAs + BB) ---
  const priceTraces = useMemo(() => {
    if (!ohlcv || !indicators) return [];
    const dates = ohlcv.data.map((d) => d.date);
    const closes = ohlcv.data.map((d) => d.close);
    const colors = ohlcv.data.map((d) =>
      d.close >= d.open
        ? "rgba(16,185,129,0.4)"
        : "rgba(239,68,68,0.4)",
    );
    return [
      // Candlestick
      {
        x: dates,
        open: ohlcv.data.map((d) => d.open),
        high: ohlcv.data.map((d) => d.high),
        low: ohlcv.data.map((d) => d.low),
        close: closes,
        type: "candlestick",
        name: "OHLC",
        yaxis: "y",
        increasing: {
          line: { color: "#10b981", width: 1.5 },
          fillcolor: "#10b981",
        },
        decreasing: {
          line: { color: "#ef4444", width: 1.5 },
          fillcolor: "#ef4444",
        },
        whiskerwidth: 0.8,
        hoverinfo: "x+text",
        text: ohlcv.data.map(
          (d) =>
            `O: ${sym}${d.open.toFixed(2)}<br>` +
            `H: ${sym}${d.high.toFixed(2)}<br>` +
            `L: ${sym}${d.low.toFixed(2)}<br>` +
            `C: ${sym}${d.close.toFixed(2)}`,
        ),
      },
      // Volume bars (secondary y-axis)
      {
        x: dates,
        y: ohlcv.data.map((d) => d.volume),
        type: "bar",
        name: "Volume",
        yaxis: "y2",
        marker: { color: colors },
        hovertemplate:
          "Vol: %{y:,.0f}<extra></extra>",
      },
      // Bollinger upper band
      {
        x: dates,
        y: indicators.data.map((d) => d.bb_upper),
        type: "scatter",
        mode: "lines",
        name: "BB Upper",
        line: { color: "rgba(99,102,241,0.25)", width: 1 },
        showlegend: false,
      },
      // Bollinger lower band (with fill to upper)
      {
        x: dates,
        y: indicators.data.map((d) => d.bb_lower),
        type: "scatter",
        mode: "lines",
        name: "Bollinger",
        fill: "tonexty",
        fillcolor: "rgba(99,102,241,0.06)",
        line: { color: "rgba(99,102,241,0.25)", width: 1 },
      },
      // SMA 50
      {
        x: dates,
        y: indicators.data.map((d) => d.sma_50),
        type: "scatter",
        mode: "lines",
        name: "SMA 50",
        line: {
          color: CHART_COLORS[3],
          width: 1.5,
          dash: "dot",
        },
      },
      // SMA 200
      {
        x: dates,
        y: indicators.data.map((d) => d.sma_200),
        type: "scatter",
        mode: "lines",
        name: "SMA 200",
        line: {
          color: CHART_COLORS[4],
          width: 1.5,
          dash: "dash",
        },
      },
    ] as Plotly.Data[];
  }, [ohlcv, indicators]);

  // --- RSI chart traces ---
  const rsiTraces = useMemo(() => {
    if (!indicators) return [];
    const dates = indicators.data.map((d) => d.date);
    return [
      {
        x: dates,
        y: indicators.data.map((d) => d.rsi_14),
        type: "scatter",
        mode: "lines",
        name: "RSI 14",
        line: { color: CHART_COLORS[1], width: 1.5 },
        hovertemplate:
          "%{x}<br>RSI: %{y:.1f}<extra></extra>",
      },
    ] as Plotly.Data[];
  }, [indicators]);

  const rsiLayout = useMemo(
    () => ({
      yaxis: { range: [0, 100] },
      shapes: [
        {
          type: "line" as const,
          x0: 0,
          x1: 1,
          xref: "paper" as const,
          y0: 70,
          y1: 70,
          line: {
            color: "#ef4444",
            width: 1,
            dash: "dot" as const,
          },
        },
        {
          type: "line" as const,
          x0: 0,
          x1: 1,
          xref: "paper" as const,
          y0: 30,
          y1: 30,
          line: {
            color: "#10b981",
            width: 1,
            dash: "dot" as const,
          },
        },
      ],
    }),
    [],
  );

  // --- MACD chart traces ---
  const macdTraces = useMemo(() => {
    if (!indicators) return [];
    const dates = indicators.data.map((d) => d.date);
    const hist = indicators.data.map(
      (d) => d.macd_hist ?? 0,
    );
    return [
      {
        x: dates,
        y: indicators.data.map((d) => d.macd),
        type: "scatter",
        mode: "lines",
        name: "MACD",
        line: { color: CHART_COLORS[0], width: 1.5 },
      },
      {
        x: dates,
        y: indicators.data.map((d) => d.macd_signal),
        type: "scatter",
        mode: "lines",
        name: "Signal",
        line: {
          color: CHART_COLORS[3],
          width: 1.5,
          dash: "dot",
        },
      },
      {
        x: dates,
        y: hist,
        type: "bar",
        name: "Histogram",
        marker: {
          color: hist.map((v) =>
            v >= 0 ? "#10b981" : "#ef4444",
          ),
        },
      },
    ] as Plotly.Data[];
  }, [indicators]);

  // --- Stats ---
  const stats = useMemo(() => {
    if (!ohlcv || !indicators) return null;
    const last = ohlcv.data[ohlcv.data.length - 1];
    const prev =
      ohlcv.data.length > 1
        ? ohlcv.data[ohlcv.data.length - 2]
        : last;
    const change = last.close - prev.close;
    const changePct =
      prev.close !== 0
        ? (change / prev.close) * 100
        : 0;
    const lastInd =
      indicators.data[indicators.data.length - 1];
    return { last, change, changePct, lastInd };
  }, [ohlcv, indicators]);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <ChartSkeleton key={i} h="h-20" />
          ))}
        </div>
        <ChartSkeleton />
        <ChartSkeleton />
        <ChartSkeleton />
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="
          rounded-lg border border-red-200
          dark:border-red-800 bg-red-50
          dark:bg-red-900/20 px-5 py-10
          text-center text-sm text-red-600
          dark:text-red-400
        "
      >
        {error}
      </div>
    );
  }

  if (!stats) return null;

  const changeColor =
    stats.change >= 0
      ? "text-emerald-600 dark:text-emerald-400"
      : "text-red-600 dark:text-red-400";

  const rsiVal = stats.lastInd?.rsi_14;
  let rsiColor = "text-gray-900 dark:text-gray-100";
  if (rsiVal != null) {
    if (rsiVal >= 70) {
      rsiColor = "text-red-600 dark:text-red-400";
    } else if (rsiVal <= 30) {
      rsiColor = "text-emerald-600 dark:text-emerald-400";
    }
  }

  const macdVal = stats.lastInd?.macd;
  const sigVal = stats.lastInd?.macd_signal;
  let macdSignalLabel = "--";
  let macdColor = "text-gray-900 dark:text-gray-100";
  if (macdVal != null && sigVal != null) {
    if (macdVal > sigVal) {
      macdSignalLabel = "Bullish";
      macdColor =
        "text-emerald-600 dark:text-emerald-400";
    } else {
      macdSignalLabel = "Bearish";
      macdColor = "text-red-600 dark:text-red-400";
    }
  }

  return (
    <div className="space-y-6">
      {/* Stats cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Current Price"
          value={`${sym}${stats.last.close.toFixed(2)}`}
        />
        <StatCard
          label="Day Change"
          value={`${stats.change >= 0 ? "+" : ""}${sym}${Math.abs(stats.change).toFixed(2)}`}
          sub={`${stats.changePct >= 0 ? "+" : ""}${stats.changePct.toFixed(2)}%`}
          color={changeColor}
        />
        <StatCard
          label="RSI (14)"
          value={
            rsiVal != null ? rsiVal.toFixed(1) : "--"
          }
          sub={
            rsiVal != null
              ? rsiVal >= 70
                ? "Overbought"
                : rsiVal <= 30
                  ? "Oversold"
                  : "Neutral"
              : undefined
          }
          color={rsiColor}
        />
        <StatCard
          label="MACD Signal"
          value={macdSignalLabel}
          sub={
            macdVal != null
              ? `MACD: ${macdVal.toFixed(3)}`
              : undefined
          }
          color={macdColor}
        />
      </div>

      {/* Price chart */}
      <div
        className="
          rounded-xl border border-gray-200
          dark:border-gray-700 bg-white
          dark:bg-gray-900 shadow-sm p-4
        "
      >
        <h3
          className="
            text-sm font-semibold text-gray-900
            dark:text-gray-100 mb-2
          "
        >
          Price &amp; Moving Averages
        </h3>
        <PlotlyChart
          data={priceTraces}
          height={480}
          config={{ scrollZoom: true }}
          layout={{
            hovermode: "x unified",
            yaxis: {
              side: "right",
              domain: [0.25, 1],
            },
            yaxis2: {
              side: "right",
              domain: [0, 0.2],
              showgrid: false,
            },
            xaxis: {
              rangeslider: { visible: false },
              autorange: false,
              range: [
                new Date(
                  Date.now() - 180 * 86400000,
                ).toISOString().slice(0, 10),
                new Date().toISOString().slice(0, 10),
              ],
              rangeselector: {
                buttons: [
                  {
                    count: 3,
                    label: "3M",
                    step: "month",
                    stepmode: "backward",
                  },
                  {
                    count: 6,
                    label: "6M",
                    step: "month",
                    stepmode: "backward",
                  },
                  {
                    count: 1,
                    label: "1Y",
                    step: "year",
                    stepmode: "backward",
                  },
                  {
                    count: 2,
                    label: "2Y",
                    step: "year",
                    stepmode: "backward",
                  },
                  {
                    count: 3,
                    label: "3Y",
                    step: "year",
                    stepmode: "backward",
                  },
                  { step: "all", label: "Max" },
                ],
                font: { size: 11 },
                x: 0,
                y: 1.15,
              },
            },
          }}
        />
      </div>

      {/* RSI chart */}
      <div
        className="
          rounded-xl border border-gray-200
          dark:border-gray-700 bg-white
          dark:bg-gray-900 shadow-sm p-4
        "
      >
        <h3
          className="
            text-sm font-semibold text-gray-900
            dark:text-gray-100 mb-2
          "
        >
          RSI (14)
        </h3>
        <PlotlyChart
          data={rsiTraces}
          layout={rsiLayout}
          height={220}
        />
      </div>

      {/* MACD chart */}
      <div
        className="
          rounded-xl border border-gray-200
          dark:border-gray-700 bg-white
          dark:bg-gray-900 shadow-sm p-4
        "
      >
        <h3
          className="
            text-sm font-semibold text-gray-900
            dark:text-gray-100 mb-2
          "
        >
          MACD
        </h3>
        <PlotlyChart data={macdTraces} height={220} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Tab: Forecast
// ---------------------------------------------------------------

function ForecastTab({ ticker }: { ticker: string }) {
  const [ohlcv, setOhlcv] =
    useState<OHLCVResponse | null>(null);
  const [series, setSeries] =
    useState<ForecastSeriesResponse | null>(null);
  const [summary, setSummary] =
    useState<TickerForecast | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const q = encodeURIComponent(ticker);
    Promise.all([
      apiFetch(
        `${API_URL}/dashboard/chart/ohlcv?ticker=${q}`,
      ).then((r) => {
        if (!r.ok) throw new Error(`OHLCV: HTTP ${r.status}`);
        return r.json() as Promise<OHLCVResponse>;
      }),
      apiFetch(
        `${API_URL}/dashboard/chart/forecast-series?ticker=${q}&horizon=9`,
      ).then((r) => {
        if (!r.ok) {
          throw new Error(`Forecast: HTTP ${r.status}`);
        }
        return r.json() as Promise<ForecastSeriesResponse>;
      }),
      apiFetch(
        `${API_URL}/dashboard/forecasts/summary`,
      ).then((r) => {
        if (!r.ok) {
          throw new Error(`Summary: HTTP ${r.status}`);
        }
        return r.json() as Promise<ForecastsResponse>;
      }),
    ])
      .then(([o, fs, sum]) => {
        if (cancelled) return;
        setOhlcv(o);
        setSeries(fs);
        const match = sum.forecasts.find(
          (f) =>
            f.ticker.toUpperCase() ===
            ticker.toUpperCase(),
        );
        setSummary(match ?? null);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  // --- Forecast chart traces ---
  const forecastTraces = useMemo(() => {
    if (!ohlcv || !series) return [];
    return buildForecastChart(
      ohlcv.data.map((d) => d.date),
      ohlcv.data.map((d) => d.close),
      series.data.map((d) => d.date),
      series.data.map((d) => d.predicted),
      series.data.map((d) => d.upper),
      series.data.map((d) => d.lower),
      ticker,
    );
  }, [ohlcv, series, ticker]);

  if (loading) {
    return (
      <div className="space-y-6">
        <ChartSkeleton />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <ChartSkeleton key={i} h="h-28" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="
          rounded-lg border border-red-200
          dark:border-red-800 bg-red-50
          dark:bg-red-900/20 px-5 py-10
          text-center text-sm text-red-600
          dark:text-red-400
        "
      >
        {error}
      </div>
    );
  }

  const targets = summary?.targets ?? [];
  const sym = tickerCurrency(ticker);

  return (
    <div className="space-y-6">
      {/* Forecast chart */}
      <div
        className="
          rounded-xl border border-gray-200
          dark:border-gray-700 bg-white
          dark:bg-gray-900 shadow-sm p-4
        "
      >
        <div className="flex items-baseline gap-2 mb-2">
          <h3
            className="
              text-sm font-semibold text-gray-900
              dark:text-gray-100
            "
          >
            Prophet Forecast
          </h3>
          {summary && (
            <span
              className="
                text-xs text-gray-400
                dark:text-gray-500
              "
            >
              as of {summary.run_date}
            </span>
          )}
        </div>
        <PlotlyChart
          data={forecastTraces}
          height={360}
        />
      </div>

      {/* Forecast target cards */}
      {targets.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {targets.map((target) => {
            const isPositive = target.pct_change >= 0;
            return (
              <div
                key={target.horizon_months}
                className="
                  rounded-lg p-4
                  bg-gray-50 dark:bg-gray-800/50
                  border border-gray-100
                  dark:border-gray-700/50
                "
              >
                <p
                  className="
                    text-xs font-medium uppercase
                    tracking-wider text-gray-400
                    dark:text-gray-500 mb-1
                  "
                >
                  {target.horizon_months}-month
                </p>
                <p
                  className="
                    text-xs text-gray-500
                    dark:text-gray-400 mb-2
                  "
                >
                  {target.target_date}
                </p>
                <p
                  className="
                    font-mono text-2xl font-semibold
                    text-gray-900 dark:text-gray-100
                    mb-1
                  "
                >
                  {sym}{target.target_price.toFixed(2)}
                </p>
                <span
                  className={`
                    inline-flex items-center px-2
                    py-0.5 rounded-full text-xs
                    font-medium
                    ${
                      isPositive
                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                    }
                  `}
                >
                  {isPositive ? "+" : ""}
                  {target.pct_change.toFixed(2)}%
                </span>
                <p
                  className="
                    font-mono text-xs text-gray-400
                    dark:text-gray-500 mt-2
                  "
                >
                  {sym}{target.lower_bound.toFixed(2)}
                  {" \u2014 "}
                  {sym}{target.upper_bound.toFixed(2)}
                </p>
              </div>
            );
          })}
        </div>
      )}

      {/* Accuracy metrics */}
      {summary &&
        (summary.mae != null ||
          summary.rmse != null ||
          summary.mape != null) && (
        <div
          className="
              rounded-xl border border-gray-200
              dark:border-gray-700 bg-white
              dark:bg-gray-900 shadow-sm px-5 py-4
            "
        >
          <h3
            className="
                text-sm font-semibold text-gray-900
                dark:text-gray-100 mb-3
              "
          >
            Model Accuracy
          </h3>
          <div className="flex items-center gap-8">
            {summary.mae != null && (
              <div className="flex items-center gap-1.5">
                <span
                  className="
                      text-xs text-gray-400
                      dark:text-gray-500
                    "
                >
                  MAE
                </span>
                <span
                  className="
                      font-mono text-sm font-medium
                      text-gray-900 dark:text-gray-100
                    "
                >
                  {summary.mae.toFixed(2)}
                </span>
              </div>
            )}
            {summary.rmse != null && (
              <div className="flex items-center gap-1.5">
                <span
                  className="
                      text-xs text-gray-400
                      dark:text-gray-500
                    "
                >
                  RMSE
                </span>
                <span
                  className="
                      font-mono text-sm font-medium
                      text-gray-900 dark:text-gray-100
                    "
                >
                  {summary.rmse.toFixed(2)}
                </span>
              </div>
            )}
            {summary.mape != null && (
              <div className="flex items-center gap-1.5">
                <span
                  className="
                      text-xs text-gray-400
                      dark:text-gray-500
                    "
                >
                  MAPE
                </span>
                <span
                  className="
                      font-mono text-sm font-medium
                      text-gray-900 dark:text-gray-100
                    "
                >
                  {Number(summary.mape).toFixed(2)}%
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Tab: Compare
// ---------------------------------------------------------------

function CompareTab() {
  return <CompareContent />;
}

// ---------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------

const TABS: { id: TabId; label: string }[] = [
  { id: "analysis", label: "Analysis" },
  { id: "forecast", label: "Forecast" },
  { id: "compare", label: "Compare" },
];

// ---------------------------------------------------------------
// Inner page (needs useSearchParams inside Suspense)
// ---------------------------------------------------------------

function AnalysisPageInner() {
  const searchParams = useSearchParams();
  const tickerParam = searchParams.get("ticker");

  const [tickers, setTickers] = useState<string[]>([]);
  const [selectedTicker, setSelectedTicker] =
    useState<string>("");
  const [activeTab, setActiveTab] =
    useState<TabId>("analysis");
  const [tickersLoading, setTickersLoading] =
    useState(true);

  // Fetch user tickers
  useEffect(() => {
    let cancelled = false;

    apiFetch(`${API_URL}/users/me/tickers`)
      .then((r) => {
        if (!r.ok) {
          throw new Error(`Tickers: HTTP ${r.status}`);
        }
        return r.json();
      })
      .then((data: { tickers: string[] }) => {
        if (cancelled) return;
        const list = data.tickers ?? [];
        setTickers(list);
        // Use URL param if valid, otherwise first ticker
        if (
          tickerParam &&
          list
            .map((t: string) => t.toUpperCase())
            .includes(tickerParam.toUpperCase())
        ) {
          setSelectedTicker(tickerParam.toUpperCase());
        } else if (list.length > 0) {
          setSelectedTicker(list[0]);
        }
      })
      .catch(() => {
        // Silently fall back — user may have no tickers
      })
      .finally(() => {
        if (!cancelled) setTickersLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [tickerParam]);

  const handleTickerChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      setSelectedTicker(e.target.value);
    },
    [],
  );

  if (tickersLoading) {
    return (
      <div className="space-y-6 p-6">
        <ChartSkeleton h="h-12" />
        <ChartSkeleton />
      </div>
    );
  }

  if (tickers.length === 0 || !selectedTicker) {
    return (
      <div
        className="
          p-6 text-center text-sm text-gray-500
          dark:text-gray-400
        "
      >
        No tickers linked to your account. Add tickers
        from the{" "}
        <Link
          href="/analytics/marketplace"
          className="text-indigo-600 dark:text-indigo-400 underline"
        >
          Marketplace
        </Link>{" "}
        to get started.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Ticker selector + Tabs */}
      <div
        className="
          flex flex-col sm:flex-row
          sm:items-center sm:justify-between gap-4
        "
      >
        {/* Ticker dropdown (hidden on Compare tab) */}
        <div className={`flex items-center gap-2 ${activeTab === "compare" ? "invisible" : ""}`}>
          <label
            htmlFor="ticker-select"
            className="
              text-sm font-medium text-gray-700
              dark:text-gray-300
            "
          >
            Ticker:
          </label>
          <select
            id="ticker-select"
            value={selectedTicker}
            onChange={handleTickerChange}
            className="
              text-sm rounded-md px-3 py-1.5
              border border-gray-200
              dark:border-gray-700
              bg-white dark:bg-gray-800
              text-gray-900 dark:text-gray-100
              focus:outline-none focus:ring-2
              focus:ring-indigo-500/40
              min-w-[120px]
            "
          >
            {tickers.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        {/* Tab pills */}
        <div
          className="
            inline-flex rounded-lg
            bg-gray-100 dark:bg-gray-800 p-1
          "
        >
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`
                px-4 py-1.5 text-sm font-medium
                rounded-md transition-colors
                ${
                  activeTab === tab.id
                    ? "bg-indigo-600 text-white shadow-sm"
                    : "text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
                }
              `}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      {activeTab === "analysis" && (
        <AnalysisTab ticker={selectedTicker} />
      )}
      {activeTab === "forecast" && (
        <ForecastTab ticker={selectedTicker} />
      )}
      {activeTab === "compare" && <CompareTab />}
    </div>
  );
}

// ---------------------------------------------------------------
// Page export (Suspense boundary for useSearchParams)
// ---------------------------------------------------------------

export default function AnalysisPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6 p-6">
          <ChartSkeleton h="h-12" />
          <ChartSkeleton />
        </div>
      }
    >
      <AnalysisPageInner />
    </Suspense>
  );
}
