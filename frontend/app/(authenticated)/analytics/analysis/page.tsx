"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { CompareContent } from "../compare/page";
import { apiFetch } from "@/lib/apiFetch";
import { useTheme } from "@/hooks/useTheme";
import { API_URL } from "@/lib/config";
import {
  PlotlyChart,
} from "@/components/charts/PlotlyChart";
import {
  buildForecastChart,
  buildForecastShapes,
} from "@/components/charts/chartBuilders";
import { StockChart } from "@/components/charts/StockChart";
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
// Tab: Analysis (full-page chart with controls)
// ---------------------------------------------------------------

import {
  type IndicatorVisibility,
  type ChartInterval,
  DEFAULT_INDICATORS,
} from "@/components/charts/StockChart";

const INDICATOR_OPTIONS: {
  key: keyof IndicatorVisibility;
  label: string;
}[] = [
  { key: "sma50", label: "SMA 50" },
  { key: "sma200", label: "SMA 200" },
  { key: "bollinger", label: "Bollinger Bands" },
  { key: "volume", label: "Volume" },
  { key: "rsi", label: "RSI (14)" },
  { key: "macd", label: "MACD" },
];

const RANGE_OPTIONS = [
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
  { label: "6M", days: 180 },
  { label: "1Y", days: 365 },
  { label: "2Y", days: 730 },
  { label: "3Y", days: 1095 },
  { label: "Max", days: 0 },
];

function AnalysisTab({ ticker }: { ticker: string }) {
  const [ohlcv, setOhlcv] =
    useState<OHLCVResponse | null>(null);
  const [indicators, setIndicators] =
    useState<IndicatorsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [visibleIndicators, setVisibleIndicators] =
    useState<IndicatorVisibility>(DEFAULT_INDICATORS);
  const [showIndicatorMenu, setShowIndicatorMenu] =
    useState(false);
  const [activeRange, setActiveRange] = useState("6M");
  const [chartInterval, setChartInterval] =
    useState<ChartInterval>("D");
  // Chart height: computed once on mount, stable across
  // re-renders to avoid triggering chart rebuilds.
  const [chartHeight] = useState(() =>
    typeof window !== "undefined"
      ? Math.max(400, window.innerHeight - 180)
      : 500,
  );

  // Use a ref for crosshair data to avoid re-renders
  // on every mouse move. The OHLC legend updates via
  // direct DOM mutation instead of React state.
  const crosshairRef = useRef<HTMLDivElement>(null);
  const handleCrosshair = useCallback(
    (data: {
      date: string;
      open: number;
      high: number;
      low: number;
      close: number;
      volume: number;
      overlays?: {
        name: string;
        value: number;
        color: string;
      }[];
    } | null) => {
      const el = crosshairRef.current;
      if (!el) return;
      if (!data) return; // keep last values visible
      const s = tickerCurrency(ticker);
      let html =
        `<span class="text-gray-500 dark:text-gray-400">${data.date}</span> ` +
        `O <span class="text-gray-900 dark:text-white">${s}${data.open.toFixed(2)}</span> ` +
        `H <span class="text-emerald-600 dark:text-emerald-400">${s}${data.high.toFixed(2)}</span> ` +
        `L <span class="text-red-600 dark:text-red-400">${s}${data.low.toFixed(2)}</span> ` +
        `C <span class="text-gray-900 dark:text-white">${s}${data.close.toFixed(2)}</span> ` +
        `<span class="text-gray-500 dark:text-gray-400">Vol ${(data.volume / 1e6).toFixed(1)}M</span>`;
      if (data.overlays) {
        for (const ov of data.overlays) {
          html +=
            ` <span class="inline-block w-2 h-2 rounded-full mr-0.5" style="background:${ov.color}"></span>` +
            `<span class="text-gray-500 dark:text-gray-400">${ov.name}</span> ` +
            `<span class="text-gray-900 dark:text-white">${ov.value.toFixed(2)}</span>`;
        }
      }
      el.innerHTML = html;
    },
    [ticker],
  );

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

  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  // Filter OHLCV by selected range.
  // Compute cutoff from the data's last date (pure).
  const chartOhlcv = useMemo(() => {
    const all =
      ohlcv?.data.map((d) => ({
        date: d.date,
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
        volume: d.volume,
      })) ?? [];
    if (all.length === 0) return all;
    const opt = RANGE_OPTIONS.find(
      (r) => r.label === activeRange,
    );
    if (!opt || opt.days === 0) return all;
    // Use the last data point as "today" (pure)
    const lastDate = new Date(
      all[all.length - 1].date,
    );
    const cutoff = new Date(
      lastDate.getTime() - opt.days * 86400000,
    )
      .toISOString()
      .slice(0, 10);
    return all.filter((d) => d.date >= cutoff);
  }, [ohlcv, activeRange]);

  const chartIndicators = useMemo(
    () =>
      indicators?.data.map((d) => ({
        date: d.date,
        sma_50: d.sma_50,
        sma_200: d.sma_200,
        rsi_14: d.rsi_14,
        macd: d.macd,
        macd_signal: d.macd_signal,
        macd_hist: d.macd_hist,
        bb_upper: d.bb_upper,
        bb_lower: d.bb_lower,
      })) ?? [],
    [indicators],
  );

  const toggleIndicator = useCallback(
    (key: keyof IndicatorVisibility) => {
      setVisibleIndicators((prev) => ({
        ...prev,
        [key]: !prev[key],
      }));
    },
    [],
  );

  // Set initial OHLC legend from latest data point
  const latest = ohlcv?.data?.[ohlcv.data.length - 1];
  useEffect(() => {
    if (latest) {
      handleCrosshair({
        date: latest.date,
        open: latest.open,
        high: latest.high,
        low: latest.low,
        close: latest.close,
        volume: latest.volume,
      });
    }
  }, [latest, handleCrosshair]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-xl bg-gray-200 dark:bg-gray-800 h-[700px]" />
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-5 py-10 text-center text-sm text-red-600 dark:text-red-400">
        {error}
      </div>
    );
  }

  return (
    <div
      className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm overflow-hidden"
    >
      {/* Chart header: OHLC legend + controls */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-gray-100 dark:border-gray-800">
        {/* OHLC legend (top-left) — updated via ref, no re-renders */}
        <div className="flex items-center gap-3 text-xs font-mono">
          <span className="font-semibold text-gray-900 dark:text-gray-100">
            {ticker}
          </span>
          <span
            ref={crosshairRef}
            className="text-gray-600 dark:text-gray-300"
          />
        </div>

        {/* Controls (right) */}
        <div className="flex items-center gap-2">
          {/* Date range pills */}
          <div className="inline-flex rounded-md bg-gray-100 dark:bg-gray-800 p-0.5">
            {RANGE_OPTIONS.map((r) => (
              <button
                key={r.label}
                onClick={() => setActiveRange(r.label)}
                className={`px-2 py-0.5 text-[10px] font-medium rounded transition-colors ${
                  activeRange === r.label
                    ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                    : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>

          {/* Interval selector (D/W/M) */}
          <div className="inline-flex rounded-md bg-gray-100 dark:bg-gray-800 p-0.5">
            {(
              [
                { key: "D", label: "Daily" },
                { key: "W", label: "Weekly" },
                { key: "M", label: "Monthly" },
              ] as { key: ChartInterval; label: string }[]
            ).map((iv) => (
              <button
                key={iv.key}
                onClick={() => setChartInterval(iv.key)}
                className={`px-2 py-0.5 text-[10px] font-medium rounded transition-colors ${
                  chartInterval === iv.key
                    ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                    : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                }`}
              >
                {iv.key}
              </button>
            ))}
          </div>

          {/* Indicators dropdown */}
          <div className="relative">
            <button
              onClick={() => setShowIndicatorMenu((v) => !v)}
              className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 transition-colors"
            >
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20V10M18 20V4M6 20v-4" /></svg>
              Indicators
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg>
            </button>
            {showIndicatorMenu && (
              <div className="absolute right-0 top-full mt-1 z-50 w-44 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg py-1">
                {INDICATOR_OPTIONS.map((opt) => (
                  <label
                    key={opt.key}
                    className="flex items-center gap-2 px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={visibleIndicators[opt.key]}
                      onChange={() => toggleIndicator(opt.key)}
                      className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Chart (full width, no padding) */}
      <StockChart
        ohlcv={chartOhlcv}
        indicators={chartIndicators}
        isDark={isDark}
        height={chartHeight}
        interval={chartInterval}
        visibleIndicators={visibleIndicators}
        onCrosshairMove={handleCrosshair}
      />
    </div>
  );
}

// ---------------------------------------------------------------
// Tab: Forecast
// ---------------------------------------------------------------

type HorizonId = 3 | 6 | 9;

function ForecastTab({ ticker }: { ticker: string }) {
  const [ohlcv, setOhlcv] =
    useState<OHLCVResponse | null>(null);
  const [series, setSeries] =
    useState<ForecastSeriesResponse | null>(null);
  const [summary, setSummary] =
    useState<TickerForecast | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [horizon, setHorizon] =
    useState<HorizonId>(9);

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

  // Truncate forecast series to selected horizon
  const truncatedSeries = useMemo(() => {
    if (!series || !series.data.length) return series;
    // 9M data ≈ ~270 points; scale by horizon ratio
    const maxPoints = Math.ceil(
      (series.data.length * horizon) / 9,
    );
    return {
      ...series,
      data: series.data.slice(0, maxPoints),
    };
  }, [series, horizon]);

  // --- Forecast chart traces ---
  const forecastTraces = useMemo(() => {
    if (!ohlcv || !truncatedSeries) return [];
    return buildForecastChart(
      ohlcv.data.map((d) => d.date),
      ohlcv.data.map((d) => d.close),
      truncatedSeries.data.map((d) => d.date),
      truncatedSeries.data.map((d) => d.predicted),
      truncatedSeries.data.map((d) => d.upper),
      truncatedSeries.data.map((d) => d.lower),
      ticker,
      summary?.sentiment,
    );
  }, [ohlcv, truncatedSeries, ticker, summary?.sentiment]);

  // --- Shapes + annotations (today line, price, targets) ---
  const { shapes, annotations } = useMemo(() => {
    const currentPrice =
      ohlcv && ohlcv.data.length > 0
        ? ohlcv.data[ohlcv.data.length - 1].close
        : null;
    // Only show targets up to selected horizon
    const targets = (summary?.targets ?? [])
      .filter((t) => t.horizon_months <= horizon)
      .map((t) => ({
        horizon_months: t.horizon_months,
        target_date: t.target_date,
        target_price: t.target_price,
        pct_change: t.pct_change,
      }));
    return buildForecastShapes(currentPrice, targets);
  }, [ohlcv, summary, horizon]);

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

  const targets = (summary?.targets ?? []).filter(
    (t) => t.horizon_months <= horizon,
  );
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
        <div
          className="
            flex flex-col sm:flex-row
            sm:items-center sm:justify-between
            gap-2 mb-3
          "
        >
          <div className="flex items-baseline gap-2">
            <h3
              className="
                text-sm font-semibold text-gray-900
                dark:text-gray-100
              "
            >
              Prophet Forecast
              {summary?.sentiment && (
                <span className="ml-1">
                  {summary.sentiment.toLowerCase().includes("bull")
                    ? "\u{1F7E2}"
                    : summary.sentiment.toLowerCase().includes("bear")
                      ? "\u{1F534}"
                      : "\u{1F7E1}"}
                  {" "}
                  {summary.sentiment}
                </span>
              )}
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
          {/* Horizon picker */}
          <div
            className="
              inline-flex rounded-lg
              bg-gray-100 dark:bg-gray-800 p-1
            "
          >
            {([3, 6, 9] as HorizonId[]).map((h) => (
              <button
                key={h}
                onClick={() => setHorizon(h)}
                className={`
                  px-3 py-1 text-xs font-medium
                  rounded-md transition-colors
                  ${
                    horizon === h
                      ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                      : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  }
                `}
              >
                {h}M
              </button>
            ))}
          </div>
        </div>
        <PlotlyChart
          data={forecastTraces}
          height={550}
          config={{ scrollZoom: true }}
          layout={{
            hovermode: "x unified",
            margin: { t: 30, r: 80, b: 40, l: 60 },
            shapes,
            annotations,
            xaxis: {
              rangeslider: { visible: false },
            },
            yaxis: {
              side: "right",
              tickformat: ",.0f",
            },
            legend: {
              orientation: "h",
              x: 0.5,
              xanchor: "center",
              y: 1.08,
              font: { size: 11 },
            },
          }}
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

  // Searchable ticker dropdown state
  const [tickerSearch, setTickerSearch] = useState("");
  const [showTickerDropdown, setShowTickerDropdown] =
    useState(false);

  const filteredTickers = useMemo(() => {
    if (!tickerSearch.trim()) return tickers;
    const q = tickerSearch.trim().toUpperCase();
    return tickers.filter((t) => t.includes(q));
  }, [tickers, tickerSearch]);

  const selectTicker = useCallback(
    (t: string) => {
      setSelectedTicker(t);
      setTickerSearch("");
      setShowTickerDropdown(false);
    },
    [],
  );

  if (tickersLoading) {
    return (
      <div className="animate-pulse rounded-xl bg-gray-200 dark:bg-gray-800 h-[700px]" />
    );
  }

  if (tickers.length === 0 || !selectedTicker) {
    return (
      <div className="p-6 text-center text-sm text-gray-500 dark:text-gray-400">
        No tickers linked.{" "}
        <Link
          href="/analytics/marketplace"
          className="text-indigo-600 dark:text-indigo-400 underline"
        >
          Link tickers
        </Link>{" "}
        to get started.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header: Tabs (left) + Ticker search (right) */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        {/* Tab pills — LEFT */}
        <div className="inline-flex rounded-lg bg-gray-100 dark:bg-gray-800 p-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                activeTab === tab.id
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Searchable ticker — RIGHT */}
        <div
          className={`relative ${activeTab === "compare" ? "invisible" : ""}`}
        >
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
              Ticker
            </span>
            <div className="relative">
              <input
                type="text"
                value={
                  showTickerDropdown
                    ? tickerSearch
                    : selectedTicker
                }
                onChange={(e) => {
                  setTickerSearch(
                    e.target.value.toUpperCase(),
                  );
                  setShowTickerDropdown(true);
                }}
                onFocus={() =>
                  setShowTickerDropdown(true)
                }
                onBlur={() =>
                  setTimeout(
                    () =>
                      setShowTickerDropdown(false),
                    200,
                  )
                }
                placeholder="Search..."
                className="w-36 text-sm font-mono font-semibold rounded-md px-2.5 py-1 border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              />
              <svg
                className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400 pointer-events-none"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="11" cy="11" r="8" />
                <path d="m21 21-4.3-4.3" />
              </svg>
            </div>
          </div>
          {showTickerDropdown && (
            <div className="absolute right-0 top-full mt-1 z-50 w-48 max-h-64 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg py-1">
              {filteredTickers.length === 0 ? (
                <div className="px-3 py-2 text-xs text-gray-400">
                  No match
                </div>
              ) : (
                filteredTickers.map((t) => (
                  <button
                    key={t}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectTicker(t);
                    }}
                    className={`w-full text-left px-3 py-1.5 text-sm font-mono hover:bg-gray-50 dark:hover:bg-gray-800 ${
                      t === selectedTicker
                        ? "text-indigo-600 dark:text-indigo-400 font-semibold"
                        : "text-gray-700 dark:text-gray-300"
                    }`}
                  >
                    {t}
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      {/* Tab content — full width */}
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
