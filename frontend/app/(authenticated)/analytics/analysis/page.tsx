"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useSearchParams,
  useRouter,
} from "next/navigation";
import Link from "next/link";
import { CompareContent } from "../compare/page";
import { RecommendationHistoryTab } from
  "@/components/insights/RecommendationHistoryTab";
import { apiFetch } from "@/lib/apiFetch";
import { useTheme } from "@/hooks/useTheme";
import { API_URL } from "@/lib/config";
import dynamic from "next/dynamic";
import { usePreferences } from "@/hooks/usePreferences";

// Dynamic imports — lightweight-charts requires window/document
// Skeleton heights match each chart's rendered height to keep
// CLS ≤ 0.02 when the dynamic import resolves. Measured
// 2026-04-23: mismatched skeleton (h-64, 256 px) against
// ~480–700 px charts was the sole CLS source on
// `analysis?tab=portfolio-forecast` (0.129) and `?tab=forecast`
// (0.100).
const StockChart = dynamic(
  () =>
    import("@/components/charts/StockChart").then(
      (m) => m.StockChart,
    ),
  { ssr: false, loading: () => <ChartSkeleton h="h-[700px]" /> },
);
const ForecastChart = dynamic(
  () =>
    import("@/components/charts/ForecastChart").then(
      (m) => m.ForecastChart,
    ),
  { ssr: false, loading: () => <ChartSkeleton h="h-[550px]" /> },
);
const PortfolioChart = dynamic(
  () =>
    import("@/components/charts/PortfolioChart").then(
      (m) => m.PortfolioChart,
    ),
  { ssr: false, loading: () => <ChartSkeleton h="h-[500px]" /> },
);
const PortfolioForecastChart = dynamic(
  () =>
    import(
      "@/components/charts/PortfolioForecastChart"
    ).then((m) => m.PortfolioForecastChart),
  { ssr: false, loading: () => <ChartSkeleton h="h-[480px]" /> },
);
import type {
  OHLCVResponse,
  IndicatorsResponse,
  ForecastBacktestResponse,
  ForecastSeriesResponse,
  ForecastsResponse,
  TickerForecast,
  PortfolioPerformanceResponse,
  PortfolioForecastResponse,
} from "@/lib/types";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

type TabId =
  | "analysis"
  | "forecast"
  | "compare"
  | "recommendations"
  | "portfolio"
  | "portfolio-forecast";

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

/** Currency symbol — checks suffix + registry market. */
function tickerCurrency(
  ticker: string,
  registryMarket?: string,
): string {
  if (ticker.endsWith(".NS") || ticker.endsWith(".BO"))
    return "₹";
  if (registryMarket === "india") return "₹";
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
} from "@/components/charts/StockChart.types";

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

function AnalysisTab({
  ticker,
  market,
  prefs,
  onPrefsChange,
}: {
  ticker: string;
  market?: string;
  prefs: Record<string, unknown>;
  onPrefsChange: (v: Record<string, unknown>) => void;
}) {
  const [ohlcv, setOhlcv] =
    useState<OHLCVResponse | null>(null);
  const [indicators, setIndicators] =
    useState<IndicatorsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [visibleIndicators, setVisibleIndicators] =
    useState<IndicatorVisibility>(() => ({
      ...DEFAULT_INDICATORS,
      ...((prefs.indicators as Record<string, boolean>) ?? {}),
    }));
  const [showIndicatorMenu, setShowIndicatorMenu] =
    useState(false);
  const [activeRange, setActiveRange] = useState(
    () => (prefs.range as string) ?? "6M",
  );
  const [chartInterval, setChartInterval] =
    useState<ChartInterval>(
      () => (prefs.interval as ChartInterval) ?? "D",
    );
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
      const s = tickerCurrency(ticker, market);
      const o = data.open ?? 0;
      const h = data.high ?? 0;
      const l = data.low ?? 0;
      const c = data.close ?? 0;
      const v = data.volume ?? 0;
      let html =
        `<span class="text-gray-500 dark:text-gray-400">${data.date}</span> ` +
        `O <span class="text-gray-900 dark:text-white">${s}${o.toFixed(2)}</span> ` +
        `H <span class="text-emerald-600 dark:text-emerald-400">${s}${h.toFixed(2)}</span> ` +
        `L <span class="text-red-600 dark:text-red-400">${s}${l.toFixed(2)}</span> ` +
        `C <span class="text-gray-900 dark:text-white">${s}${c.toFixed(2)}</span> ` +
        `<span class="text-gray-500 dark:text-gray-400">Vol ${(v / 1e6).toFixed(1)}M</span>`;
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
    [ticker, market],
  );

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      setLoading(true);
      setError(null);
    });

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
      setVisibleIndicators((prev) => {
        const next = { ...prev, [key]: !prev[key] };
        onPrefsChange({ indicators: next });
        return next;
      });
    },
    [onPrefsChange],
  );

  // Persist range and interval changes
  const handleRange = useCallback(
    (r: string) => {
      setActiveRange(r);
      onPrefsChange({ range: r });
    },
    [onPrefsChange],
  );
  const handleInterval = useCallback(
    (iv: ChartInterval) => {
      setChartInterval(iv);
      onPrefsChange({ interval: iv });
    },
    [onPrefsChange],
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
      <div
        data-testid="stock-analysis-error"
        className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-5 py-10 text-center text-sm text-red-600 dark:text-red-400"
      >
        {error}
      </div>
    );
  }

  return (
    <div
      data-testid="stock-analysis-chart"
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
                data-testid={
                  `stock-analysis-range-${r.label.toLowerCase()}`
                }
                onClick={() => handleRange(r.label)}
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
                data-testid={
                  `stock-analysis-interval-${iv.key.toLowerCase()}`
                }
                onClick={() => handleInterval(iv.key)}
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
              data-testid="stock-analysis-indicators-menu"
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
                    data-testid={
                      `stock-analysis-indicator-${opt.key}`
                    }
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

function ForecastTab({
  ticker,
  market,
}: {
  ticker: string;
  market?: string;
}) {
  const [ohlcv, setOhlcv] =
    useState<OHLCVResponse | null>(null);
  const [series, setSeries] =
    useState<ForecastSeriesResponse | null>(null);
  const [summary, setSummary] =
    useState<TickerForecast | null>(null);
  const [backtest, setBacktest] =
    useState<ForecastBacktestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [horizon, setHorizon] =
    useState<HorizonId>(9);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      setLoading(true);
      setError(null);
    });

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
        `${API_URL}/dashboard/forecasts/summary?ticker=${encodeURIComponent(q)}`,
      ).then((r) => {
        if (!r.ok) {
          throw new Error(`Summary: HTTP ${r.status}`);
        }
        return r.json() as Promise<ForecastsResponse>;
      }),
      apiFetch(
        `${API_URL}/dashboard/chart/forecast-backtest?ticker=${q}`,
      ).then((r) => {
        if (!r.ok) return null;
        return r.json() as Promise<ForecastBacktestResponse>;
      }).catch(() => null),
    ])
      .then(([o, fs, sum, bt]) => {
        if (cancelled) return;
        setOhlcv(o);
        setSeries(fs);
        setBacktest(bt);
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

  const { resolvedTheme: fcTheme } = useTheme();
  const fcIsDark = fcTheme === "dark";

  // Crosshair tooltip ref
  const fcTooltip = useRef<HTMLDivElement>(null);
  const handleFcMove = useCallback(
    (info: {
      date: string;
      price: number;
      isForecast: boolean;
      lower?: number;
      upper?: number;
      backtestPredicted?: number;
    } | null) => {
      const el = fcTooltip.current;
      if (!el || !info || info.price == null) return;
      const s = tickerCurrency(ticker, market);
      const tag = info.isForecast
        ? '<span class="text-emerald-500 text-[9px]">FORECAST</span> '
        : "";
      // NOTE: all values are numeric (from chart data),
      // not user input — safe for innerHTML.
      let html =
        `<span class="text-gray-500 dark:text-gray-400">${info.date}</span> `
        + tag
        + `<span class="text-gray-900 dark:text-white font-semibold">${s}${info.price.toFixed(2)}</span>`;
      if (info.lower != null && info.upper != null) {
        html +=
          ` <span class="text-gray-400 dark:text-gray-500">${s}${info.lower.toFixed(2)} \u2014 ${s}${info.upper.toFixed(2)}</span>`;
      }
      if (
        info.backtestPredicted != null
        && !info.isForecast
      ) {
        const diff = info.backtestPredicted - info.price;
        const pct = (
          (diff / info.price) * 100
        ).toFixed(1);
        const sign = diff >= 0 ? "+" : "";
        html +=
          ` <span class="text-orange-500">`
          + `Backtest: ${s}${info.backtestPredicted.toFixed(2)}`
          + ` (${sign}${pct}%)</span>`;
      }
      el.innerHTML = html;
    },
    [ticker, market],
  );

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
        data-testid="stock-forecast-error"
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
  const sym = tickerCurrency(ticker, market);

  return (
    <div className="space-y-6">
      {/* Forecast chart */}
      <div
        data-testid="stock-forecast-chart"
        className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm overflow-hidden"
      >
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
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
            {summary?.confidence_components?.badge && (
              <span
                className={`
                  inline-flex items-center px-2 py-0.5
                  rounded-full text-xs font-medium
                  ${summary.confidence_components.badge === "High"
                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                    : summary.confidence_components.badge === "Medium"
                    ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400"
                    : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                  }
                `}
                title={`Confidence: ${((summary.confidence_score ?? 0) * 100).toFixed(0)}%${summary.confidence_components.reason ? ` — ${summary.confidence_components.reason}` : ""}`}
              >
                {summary.confidence_components.badge}
              </span>
            )}
            {summary && (
              <span className="text-xs text-gray-400 dark:text-gray-500">
                as of {summary.run_date}
              </span>
            )}
            <span
              ref={fcTooltip}
              className="text-xs font-mono text-gray-600 dark:text-gray-300"
            />
            <div className="flex items-center gap-3 text-[10px] text-gray-400 dark:text-gray-500">
              <span className="flex items-center gap-1">
                <span className="inline-block w-4 h-0.5 bg-indigo-500" />
                Historical
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-4 h-0.5 border-t-2 border-dashed border-emerald-500" />
                Forecast
              </span>
            </div>
          </div>
          {/* Horizon picker */}
          <div className="inline-flex rounded-lg bg-gray-100 dark:bg-gray-800 p-1">
            {([3, 6, 9] as HorizonId[]).map((h) => (
              <button
                key={h}
                data-testid={
                  `stock-forecast-horizon-${h}`
                }
                onClick={() => setHorizon(h)}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                  horizon === h
                    ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                    : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                }`}
              >
                {h}M
              </button>
            ))}
          </div>
        </div>
        {/* Suspense boundary so the chart's hydration
            cost (Plotly init + dataset processing,
            historically 6-7s on this tab) doesn't
            block hydration of the rest of the route.
            React 19 streams the surrounding tree in;
            the chart resolves into the boundary as
            its dynamic chunk arrives. (ASETPLTFRM-334
            phase B) */}
        <Suspense fallback={<ChartSkeleton h="h-[550px]" />}>
        <ForecastChart
          historicalDates={
            ohlcv?.data.map((d) => d.date) ?? []
          }
          historicalPrices={
            ohlcv?.data.map((d) => d.close) ?? []
          }
          forecastDates={
            truncatedSeries?.data.map(
              (d) => d.date,
            ) ?? []
          }
          forecastPredicted={
            truncatedSeries?.data.map(
              (d) => d.predicted,
            ) ?? []
          }
          forecastUpper={
            truncatedSeries?.data.map(
              (d) => d.upper,
            ) ?? []
          }
          forecastLower={
            truncatedSeries?.data.map(
              (d) => d.lower,
            ) ?? []
          }
          backtestDates={
            backtest?.data.map(
              (d) => d.date,
            ) ?? []
          }
          backtestPredicted={
            backtest?.data.map(
              (d) => d.predicted,
            ) ?? []
          }
          isDark={fcIsDark}
          height={550}
          onCrosshairMove={handleFcMove}
        />
        </Suspense>
      </div>

      {/* Forecast target cards */}
      {targets.length > 0 && (() => {
        const isExtreme = targets.some(
          (t) => Math.abs(t.pct_change) > 200,
        );
        if (isExtreme) {
          return (
            <div
              className="
                p-4 rounded-lg border
                bg-amber-50 dark:bg-amber-900/20
                border-amber-200 dark:border-amber-800
                text-amber-700 dark:text-amber-400
                text-sm
              "
            >
              <p className="font-medium mb-1">
                Low confidence forecast
              </p>
              <p className="text-xs">
                This ticker&apos;s forecast shows extreme
                predictions (&gt;200% deviation) which are
                unreliable. The model struggles with
                highly volatile price histories.
              </p>
            </div>
          );
        }
        return (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {targets.map((target, idx) => {
            const isPositive = target.pct_change >= 0;
            return (
              <div
                key={target.horizon_months}
                data-testid={
                  `stock-forecast-target-card-${idx}`
                }
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
        );
      })()}

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
              <div
                data-testid="stock-forecast-accuracy-mae"
                className="flex items-center gap-1.5"
              >
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
              <div
                data-testid="stock-forecast-accuracy-rmse"
                className="flex items-center gap-1.5"
              >
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
              <div
                data-testid="stock-forecast-accuracy-mape"
                className="flex items-center gap-1.5"
              >
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

          {/* Extended backtest metrics */}
          {backtest?.accuracy && (
            <div className="flex items-center gap-8 mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-gray-400 dark:text-gray-500">
                  Direction
                </span>
                <span className={`font-mono text-sm font-medium ${
                  backtest.accuracy.directional_accuracy_pct >= 55
                    ? "text-emerald-600 dark:text-emerald-400"
                    : backtest.accuracy.directional_accuracy_pct >= 50
                      ? "text-amber-600 dark:text-amber-400"
                      : "text-red-600 dark:text-red-400"
                }`}>
                  {backtest.accuracy.directional_accuracy_pct.toFixed(1)}%
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-gray-400 dark:text-gray-500">
                  P50 Err
                </span>
                <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                  {backtest.accuracy.p50_error_pct.toFixed(1)}%
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-gray-400 dark:text-gray-500">
                  P90 Err
                </span>
                <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                  {backtest.accuracy.p90_error_pct.toFixed(1)}%
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-gray-400 dark:text-gray-500">
                  Max Err
                </span>
                <span className="font-mono text-sm font-medium text-red-600 dark:text-red-400">
                  {backtest.accuracy.max_error_pct.toFixed(1)}%
                </span>
              </div>
            </div>
          )}
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
// Tab: Portfolio Performance
// ---------------------------------------------------------------

const PERIOD_OPTIONS = [
  "1D", "1W", "1M", "3M", "6M", "1Y", "ALL",
] as const;

function PortfolioTab({
  marketFilter,
}: {
  marketFilter: string;
}) {
  const [data, setData] =
    useState<PortfolioPerformanceResponse | null>(
      null,
    );
  const [loading, setLoading] = useState(true);
  const [error, setError] =
    useState<string | null>(null);
  const [period, setPeriod] = useState("ALL");
  const currency =
    marketFilter === "india" ? "INR" : "USD";
  const sym = currency === "INR" ? "\u20B9" : "$";

  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  // Refresh counter to force re-fetch after
  // background refresh completes.
  const [refreshKey, setRefreshKey] = useState(0);
  type RefreshState =
    | "idle"
    | "pending"
    | "success"
    | "error";
  const [refreshState, setRefreshState] =
    useState<RefreshState>("idle");

  const startRefresh = useCallback(async () => {
    setRefreshState("pending");
    try {
      // Get portfolio tickers
      const tr = await apiFetch(
        `${API_URL}/users/me/portfolio`,
      );
      if (!tr.ok) throw new Error("fetch failed");
      const pf = await tr.json();
      const tickers: string[] = (
        pf.holdings ?? []
      ).map(
        (h: { ticker: string }) => h.ticker,
      );
      if (tickers.length === 0) {
        setRefreshState("idle");
        return;
      }

      // Start refresh for all tickers
      await Promise.all(
        tickers.map((t) =>
          apiFetch(
            `${API_URL}/dashboard/refresh/`
            + `${encodeURIComponent(t)}`,
            { method: "POST" },
          ),
        ),
      );

      // Poll until all done (max 3 min)
      const pending = new Set(tickers);
      for (let i = 0; i < 90; i++) {
        await new Promise((ok) =>
          setTimeout(ok, 2000),
        );
        for (const t of [...pending]) {
          const sr = await apiFetch(
            `${API_URL}/dashboard/refresh/`
            + `${encodeURIComponent(t)}/status`,
          );
          if (!sr.ok) continue;
          const s = await sr.json();
          if (
            s.status === "success" ||
            s.status === "error"
          ) {
            pending.delete(t);
          }
        }
        if (pending.size === 0) break;
      }

      setRefreshState("success");
      setRefreshKey((k) => k + 1);
      setTimeout(
        () => setRefreshState("idle"),
        3000,
      );
    } catch {
      setRefreshState("error");
      setTimeout(
        () => setRefreshState("idle"),
        5000,
      );
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    apiFetch(
      `${API_URL}/dashboard/portfolio/performance`
      + `?period=${period}&currency=${currency}`,
    )
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<PortfolioPerformanceResponse>;
      })
      .then((d) => {
        if (!cancelled) setData(d);
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
  }, [period, currency, refreshKey]);

  // Crosshair ref for tooltip
  const tooltipRef = useRef<HTMLDivElement>(null);
  const handleCrosshair = useCallback(
    (pt: {
      date: string;
      value: number;
      invested_value: number;
      daily_pnl: number;
      daily_return_pct: number;
    } | null) => {
      const el = tooltipRef.current;
      if (!el || !pt) return;
      const pos = pt.daily_pnl >= 0;
      const gl = pt.invested_value > 0
        ? ((pt.value - pt.invested_value) / pt.invested_value * 100)
        : 0;
      const glPos = gl >= 0;
      el.innerHTML =
        `<span class="text-gray-500 dark:text-gray-400">${pt.date}</span> `
        + `<span class="text-gray-900 dark:text-white font-semibold">${sym}${pt.value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span> `
        + `<span class="text-gray-400 dark:text-gray-500">Inv ${sym}${pt.invested_value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span> `
        + `<span class="${glPos ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}">`
        + `${glPos ? "+" : ""}${gl.toFixed(2)}%</span> `
        + `<span class="${pos ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}">`
        + `${pos ? "+" : ""}${sym}${pt.daily_pnl.toFixed(2)}</span>`;
    },
    [sym],
  );

  if (loading) {
    return <ChartSkeleton h="h-[500px]" />;
  }

  if (error) {
    return (
      <div
        data-testid="portfolio-analysis-error"
        className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-5 py-10 text-center text-sm text-red-600 dark:text-red-400"
      >
        {error}
      </div>
    );
  }

  if (!data || data.data.length === 0) {
    return (
      <div
        data-testid="portfolio-analysis-empty"
        className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 px-5 py-10 text-center text-sm text-gray-500 dark:text-gray-400"
      >
        Add stocks to your portfolio to see
        performance.{" "}
        <Link
          href="/dashboard"
          className="text-indigo-600 dark:text-indigo-400 underline"
        >
          Go to Dashboard
        </Link>
      </div>
    );
  }

  const m = data.metrics;

  return (
    <div className="space-y-4">
      {/* Chart card */}
      <div
        data-testid="portfolio-analysis-chart"
        className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm overflow-hidden"
      >
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-3 text-xs font-mono">
            <span
              data-testid="portfolio-analysis-currency-badge"
              className="font-semibold text-gray-900 dark:text-gray-100"
            >
              Portfolio ({currency})
            </span>
            <span
              ref={tooltipRef}
              className="text-gray-600 dark:text-gray-300"
            />
            <div className="flex items-center gap-3 text-[10px] text-gray-400 dark:text-gray-500">
              <span className="flex items-center gap-1">
                <span className="inline-block w-4 h-0.5 bg-indigo-500" />
                Market Value
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-4 h-0.5 border-t-2 border-dashed border-amber-500" />
                Invested
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Refresh button */}
            <button
              data-testid="portfolio-analysis-refresh-btn"
              onClick={startRefresh}
              disabled={refreshState === "pending"}
              title={
                refreshState === "pending"
                  ? "Refreshing..."
                  : refreshState === "success"
                    ? "Updated!"
                    : refreshState === "error"
                      ? "Refresh failed"
                      : "Refresh portfolio data"
              }
              className="p-1 rounded-md text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-50"
            >
              {refreshState === "pending" ? (
                <svg data-testid="portfolio-analysis-refresh-icon" className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                </svg>
              ) : refreshState === "success" ? (
                <svg data-testid="portfolio-analysis-refresh-icon" className="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              ) : refreshState === "error" ? (
                <svg data-testid="portfolio-analysis-refresh-icon" className="w-4 h-4 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              ) : (
                <svg data-testid="portfolio-analysis-refresh-icon" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                  <path d="M3 3v5h5" />
                  <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
                  <path d="M16 16h5v5" />
                </svg>
              )}
            </button>
            {/* Period pills */}
            <div className="inline-flex rounded-md bg-gray-100 dark:bg-gray-800 p-0.5">
              {PERIOD_OPTIONS.map((p) => (
                <button
                  key={p}
                  data-testid={
                    `portfolio-analysis-period-${p.toLowerCase()}`
                  }
                  onClick={() => setPeriod(p)}
                  className={`px-2 py-0.5 text-[10px] font-medium rounded transition-colors ${
                    period === p
                      ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                      : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        </div>
        <PortfolioChart
          data={data.data}
          isDark={isDark}
          height={Math.max(
            400,
            typeof window !== "undefined"
              ? window.innerHeight - 280
              : 500,
          )}
          onCrosshairMove={handleCrosshair}
        />
      </div>

      {/* Metrics cards */}
      {m && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            {
              tid: "totalReturn",
              label: "Total Return",
              value: `${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct.toFixed(2)}%`,
              positive: m.total_return_pct >= 0,
            },
            {
              tid: "annualized",
              label: "Annualized",
              value: `${m.annualized_return_pct >= 0 ? "+" : ""}${m.annualized_return_pct.toFixed(2)}%`,
              positive:
                m.annualized_return_pct >= 0,
            },
            {
              tid: "maxDrawdown",
              label: "Max Drawdown",
              value: `${m.max_drawdown_pct.toFixed(2)}%`,
              positive: false,
            },
            {
              tid: "sharpe",
              label: "Sharpe Ratio",
              value:
                m.sharpe_ratio != null
                  ? m.sharpe_ratio.toFixed(2)
                  : "N/A",
              positive:
                m.sharpe_ratio != null &&
                m.sharpe_ratio > 0,
            },
            {
              tid: "bestDay",
              label: `Best Day (${m.best_day_date})`,
              value: `+${m.best_day_pct.toFixed(2)}%`,
              positive: true,
            },
            {
              tid: "worstDay",
              label: `Worst Day (${m.worst_day_date})`,
              value: `${m.worst_day_pct.toFixed(2)}%`,
              positive: false,
            },
          ].map((card) => (
            <div
              key={card.label}
              data-testid={
                `portfolio-analysis-metric-${card.tid}`
              }
              className="rounded-lg p-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50"
            >
              <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 mb-1">
                {card.label}
              </p>
              <p
                data-testid={
                  `portfolio-analysis-metric-value-${card.tid}`
                }
                className={`font-mono text-lg font-semibold ${
                  card.positive
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-red-600 dark:text-red-400"
                }`}
              >
                {card.value}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Tab: Portfolio Forecast
// ---------------------------------------------------------------

function PortfolioForecastTab({
  marketFilter,
}: {
  marketFilter: string;
}) {
  const [perf, setPerf] =
    useState<PortfolioPerformanceResponse | null>(
      null,
    );
  const [forecast, setForecast] =
    useState<PortfolioForecastResponse | null>(
      null,
    );
  const [loading, setLoading] = useState(true);
  const [error, setError] =
    useState<string | null>(null);
  const [horizon, setHorizon] =
    useState<3 | 6 | 9>(9);
  const currency =
    marketFilter === "india" ? "INR" : "USD";
  const sym = currency === "INR" ? "\u20B9" : "$";

  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  // Refresh state
  const [fcRefreshKey, setFcRefreshKey] =
    useState(0);
  type FcRefreshState =
    | "idle"
    | "pending"
    | "success"
    | "error";
  const [fcRefreshState, setFcRefreshState] =
    useState<FcRefreshState>("idle");

  const startFcRefresh = useCallback(async () => {
    setFcRefreshState("pending");
    try {
      const tr = await apiFetch(
        `${API_URL}/users/me/portfolio`,
      );
      if (!tr.ok) throw new Error("fetch failed");
      const pf = await tr.json();
      const tickers: string[] = (
        pf.holdings ?? []
      ).map(
        (h: { ticker: string }) => h.ticker,
      );
      if (tickers.length === 0) {
        setFcRefreshState("idle");
        return;
      }
      await Promise.all(
        tickers.map((t) =>
          apiFetch(
            `${API_URL}/dashboard/refresh/`
            + `${encodeURIComponent(t)}`,
            { method: "POST" },
          ),
        ),
      );
      const pending = new Set(tickers);
      for (let i = 0; i < 90; i++) {
        await new Promise((ok) =>
          setTimeout(ok, 2000),
        );
        for (const t of [...pending]) {
          const sr = await apiFetch(
            `${API_URL}/dashboard/refresh/`
            + `${encodeURIComponent(t)}/status`,
          );
          if (!sr.ok) continue;
          const s = await sr.json();
          if (
            s.status === "success" ||
            s.status === "error"
          ) {
            pending.delete(t);
          }
        }
        if (pending.size === 0) break;
      }
      setFcRefreshState("success");
      setFcRefreshKey((k) => k + 1);
      setTimeout(
        () => setFcRefreshState("idle"),
        3000,
      );
    } catch {
      setFcRefreshState("error");
      setTimeout(
        () => setFcRefreshState("idle"),
        5000,
      );
    }
  }, []);

  // Always fetch 9M; truncate client-side
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      apiFetch(
        `${API_URL}/dashboard/portfolio/performance`
        + `?period=6M&currency=${currency}`,
      ).then((r) => {
        if (!r.ok) throw new Error(`Perf: HTTP ${r.status}`);
        return r.json() as Promise<PortfolioPerformanceResponse>;
      }),
      apiFetch(
        `${API_URL}/dashboard/portfolio/forecast`
        + `?horizon=9&currency=${currency}`,
      ).then((r) => {
        if (!r.ok) throw new Error(`Forecast: HTTP ${r.status}`);
        return r.json() as Promise<PortfolioForecastResponse>;
      }),
    ])
      .then(([p, f]) => {
        if (cancelled) return;
        setPerf(p);
        setForecast(f);
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
  }, [currency, fcRefreshKey]);

  // Client-side horizon truncation
  const truncated = useMemo(() => {
    if (!forecast?.data.length) return forecast;
    const max = Math.ceil(
      (forecast.data.length * horizon) / 9,
    );
    return {
      ...forecast,
      data: forecast.data.slice(0, max),
    };
  }, [forecast, horizon]);

  // Crosshair tooltip — hooks MUST be before
  // any early returns (Rules of Hooks).
  const fcTooltipRef =
    useRef<HTMLDivElement>(null);
  const fmtNum = useCallback(
    (n: number) =>
      n.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }),
    [],
  );
  const handleFcCrosshair = useCallback(
    (info: {
      date: string;
      value: number;
      invested: number;
      gainPct: number;
      isForecast: boolean;
    } | null) => {
      const el = fcTooltipRef.current;
      if (!el || !info) return;
      const pos = info.gainPct >= 0;
      const tag = info.isForecast
        ? '<span class="text-emerald-500 text-[9px]">FORECAST</span> '
        : "";
      el.innerHTML =
        `<span class="text-gray-500 dark:text-gray-400">${info.date}</span> `
        + tag
        + `<span class="text-gray-900 dark:text-white font-semibold">${sym}${fmtNum(info.value)}</span> `
        + `<span class="text-gray-400 dark:text-gray-500">Inv ${sym}${fmtNum(info.invested)}</span> `
        + `<span class="${pos ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}">`
        + `${pos ? "+" : ""}${info.gainPct.toFixed(2)}%</span>`;
    },
    [sym, fmtNum],
  );

  if (loading) {
    return <ChartSkeleton h="h-[500px]" />;
  }

  if (error) {
    return (
      <div
        data-testid="portfolio-forecast-error"
        className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-5 py-10 text-center text-sm text-red-600 dark:text-red-400"
      >
        {error}
      </div>
    );
  }

  if (
    !truncated ||
    truncated.data.length === 0
  ) {
    return (
      <div
        data-testid="portfolio-forecast-empty"
        className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 px-5 py-10 text-center text-sm text-gray-500 dark:text-gray-400"
      >
        Run forecasts on your holdings first.
      </div>
    );
  }

  // Summary values
  const invested = forecast?.total_invested ?? 0;
  const curVal = forecast?.current_value ?? 0;
  const endVal =
    truncated.data[truncated.data.length - 1]
      .predicted;
  // Unrealized P&L (current vs invested)
  const unrealizedPnl = curVal - invested;
  const unrealizedPct =
    invested > 0
      ? (unrealizedPnl / invested) * 100
      : 0;
  // Expected return on cost basis
  const expReturn =
    invested > 0
      ? ((endVal - invested) / invested) * 100
      : 0;

  return (
    <div className="space-y-4">
      <div
        data-testid="portfolio-forecast-chart"
        // min-height reserves space for the header bar + chart
        // canvas regardless of whether the lazy chart has loaded.
        // Without it, the 4-card grid below shifts (measured CLS
        // 0.129–0.162 on 2026-04-23) as the dynamic import arrives.
        className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm overflow-hidden min-h-[760px]"
      >
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 px-4 py-3 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Portfolio Forecast
            </h3>
            <span
              ref={fcTooltipRef}
              className="text-xs font-mono text-gray-600 dark:text-gray-300"
            />
            <div className="flex items-center gap-3 text-[10px] text-gray-400 dark:text-gray-500">
              <span className="flex items-center gap-1">
                <span className="inline-block w-4 h-0.5 bg-indigo-500" />
                Market Value
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-4 h-0.5 border-t-2 border-dashed border-amber-500" />
                Invested
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-4 h-0.5 border-t border-dashed border-emerald-500" />
                Forecast
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Refresh button */}
            <button
              data-testid="portfolio-forecast-refresh-btn"
              onClick={startFcRefresh}
              disabled={fcRefreshState === "pending"}
              title={
                fcRefreshState === "pending"
                  ? "Refreshing..."
                  : fcRefreshState === "success"
                    ? "Updated!"
                    : fcRefreshState === "error"
                      ? "Refresh failed"
                      : "Refresh portfolio data"
              }
              className="p-1 rounded-md text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-50"
            >
              {fcRefreshState === "pending" ? (
                <svg data-testid="portfolio-forecast-refresh-icon" className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                </svg>
              ) : fcRefreshState === "success" ? (
                <svg data-testid="portfolio-forecast-refresh-icon" className="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              ) : fcRefreshState === "error" ? (
                <svg data-testid="portfolio-forecast-refresh-icon" className="w-4 h-4 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              ) : (
                <svg data-testid="portfolio-forecast-refresh-icon" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                  <path d="M3 3v5h5" />
                  <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
                  <path d="M16 16h5v5" />
                </svg>
              )}
            </button>
            {/* Horizon picker */}
            <div className="inline-flex rounded-lg bg-gray-100 dark:bg-gray-800 p-1">
              {([3, 6, 9] as const).map((h) => (
                <button
                  key={h}
                  data-testid={
                    `portfolio-forecast-horizon-${h}`
                  }
                  onClick={() => setHorizon(h)}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                    horizon === h
                      ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
                      : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                  }`}
                >
                  {h}M
                </button>
              ))}
            </div>
          </div>
        </div>
        {/* See ForecastChart Suspense above —
            same rationale (ASETPLTFRM-334 phase B). */}
        <Suspense fallback={<ChartSkeleton h="h-[480px]" />}>
        <PortfolioForecastChart
          perfData={perf?.data ?? []}
          forecastData={truncated.data}
          isDark={isDark}
          height={Math.max(
            400,
            typeof window !== "undefined"
              ? window.innerHeight - 320
              : 480,
          )}
          onCrosshairMove={handleFcCrosshair}
        />
        </Suspense>
      </div>

      {/* Summary cards — 4 with explainability */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {/* Total Invested */}
        <div
          data-testid="portfolio-forecast-card-invested"
          className="rounded-lg p-4 bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50"
        >
          <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 mb-1">
            Total Invested
          </p>
          <p
            data-testid="portfolio-forecast-card-value-invested"
            className="font-mono text-xl font-semibold text-gray-900 dark:text-gray-100"
          >
            {sym}{fmtNum(invested)}
          </p>
        </div>
        {/* Current Value + unrealized P&L */}
        <div
          data-testid="portfolio-forecast-card-current"
          className="rounded-lg p-4 bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50"
        >
          <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 mb-1">
            Current Value
          </p>
          <p
            data-testid="portfolio-forecast-card-value-current"
            className="font-mono text-xl font-semibold text-gray-900 dark:text-gray-100"
          >
            {sym}{fmtNum(curVal)}
          </p>
          <p
            data-testid="portfolio-forecast-card-pnl"
            className={`text-[10px] font-mono mt-0.5 ${
              unrealizedPnl >= 0
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400"
            }`}
          >
            {unrealizedPnl >= 0 ? "+" : ""}
            {sym}{fmtNum(Math.abs(unrealizedPnl))}
            {" ("}
            {unrealizedPnl >= 0 ? "+" : ""}
            {unrealizedPct.toFixed(2)}%{")"}
          </p>
        </div>
        {/* Predicted */}
        <div
          data-testid="portfolio-forecast-card-predicted"
          className="rounded-lg p-4 bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50"
        >
          <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 mb-1">
            Predicted ({horizon}M)
          </p>
          <p
            data-testid="portfolio-forecast-card-value-predicted"
            className="font-mono text-xl font-semibold text-gray-900 dark:text-gray-100"
          >
            {sym}{fmtNum(endVal)}
          </p>
        </div>
        {/* Expected Return (on cost) */}
        <div
          data-testid="portfolio-forecast-card-return"
          className="rounded-lg p-4 bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50"
        >
          <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 mb-1">
            Expected Return (on cost)
          </p>
          <p
            data-testid="portfolio-forecast-card-value-return"
            className={`font-mono text-xl font-semibold ${
              expReturn >= 0
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400"
            }`}
          >
            {expReturn >= 0 ? "+" : ""}
            {expReturn.toFixed(2)}%
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------

const TABS: { id: TabId; label: string }[] = [
  {
    id: "portfolio",
    label: "Portfolio Analysis",
  },
  {
    id: "portfolio-forecast",
    label: "Portfolio Forecast",
  },
  { id: "analysis", label: "Stock Analysis" },
  { id: "forecast", label: "Stock Forecast" },
  { id: "compare", label: "Compare Stocks" },
  { id: "recommendations", label: "Recommendations" },
];

// ---------------------------------------------------------------
// Inner page (needs useSearchParams inside Suspense)
// ---------------------------------------------------------------

function AnalysisPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const tickerParam = searchParams.get("ticker");
  const tabParam = searchParams.get("tab") as
    | TabId
    | null;
  const [userPrefs, updatePrefs] = usePreferences();
  const chartPrefs = (userPrefs.chart ?? {}) as Record<
    string,
    unknown
  >;

  const [tickers, setTickers] = useState<string[]>([]);
  const [tickerMarkets, setTickerMarkets] = useState<
    Record<string, string>
  >({});
  const [tickerTypes, setTickerTypes] = useState<
    Record<string, string>
  >({});
  const [selectedTicker, setSelectedTicker] =
    useState<string>("");
  // URL tab param ALWAYS wins (hero buttons).
  // Otherwise fall back to preference > default.
  const resolvedTab: TabId =
    tabParam ??
    (chartPrefs.tab as TabId) ??
    "analysis";
  const [activeTab, setActiveTab] =
    useState<TabId>(resolvedTab);

  // Force-sync when URL param changes (hero nav
  // on an already-mounted component)
  const prevTabParam = useRef(tabParam);
  useEffect(() => {
    if (
      tabParam &&
      tabParam !== prevTabParam.current
    ) {
      setActiveTab(tabParam);
    }
    prevTabParam.current = tabParam;
  }, [tabParam]);

  const [tickersLoading, setTickersLoading] =
    useState(true);

  // Capture initial saved-ticker pref in a ref so the
  // tickers fetch effect doesn't re-run when chartPrefs
  // changes (it only reads it once on first load).
  const initialSavedTickerRef = useRef<string | undefined>(
    chartPrefs.ticker as string | undefined,
  );

  // Fetch user tickers + registry tickers (merged)
  useEffect(() => {
    let cancelled = false;

    Promise.all([
      apiFetch(`${API_URL}/users/me/tickers`)
        .then((r) =>
          r.ok ? r.json() : { tickers: [] },
        )
        .catch(() => ({ tickers: [] })),
      apiFetch(`${API_URL}/dashboard/registry`)
        .then((r) =>
          r.ok ? r.json() : { tickers: [] },
        )
        .catch(() => ({ tickers: [] })),
    ])
      .then(([userData, regData]) => {
        if (cancelled) return;
        const userList: string[] =
          userData.tickers ?? [];
        const regTickers = (regData.tickers ?? []) as {
          ticker: string;
          market?: string;
          ticker_type?: string;
        }[];
        const regList: string[] = regTickers.map(
          (t) => t.ticker,
        );
        // Build ticker → market/type lookups
        const mktMap: Record<string, string> = {};
        const typeMap: Record<string, string> = {};
        for (const t of regTickers) {
          if (t.market) mktMap[t.ticker] = t.market;
          if (t.ticker_type)
            typeMap[t.ticker] = t.ticker_type;
        }
        setTickerMarkets(mktMap);
        setTickerTypes(typeMap);
        // Merge: user tickers first, then registry
        const seen = new Set(
          userList.map((t: string) =>
            t.toUpperCase(),
          ),
        );
        const merged = [...userList];
        for (const t of regList) {
          if (!seen.has(t.toUpperCase())) {
            merged.push(t);
            seen.add(t.toUpperCase());
          }
        }
        setTickers(merged);
        // Priority: URL param > saved pref > first
        const upper = merged.map(
          (t: string) => t.toUpperCase(),
        );
        const savedTicker = initialSavedTickerRef.current;
        if (
          tickerParam &&
          upper.includes(tickerParam.toUpperCase())
        ) {
          setSelectedTicker(
            tickerParam.toUpperCase(),
          );
        } else if (
          savedTicker &&
          upper.includes(savedTicker.toUpperCase())
        ) {
          setSelectedTicker(
            savedTicker.toUpperCase(),
          );
        } else if (merged.length > 0) {
          setSelectedTicker(merged[0]);
        }
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

  const isForecastTab =
    activeTab === "forecast" ||
    activeTab === "portfolio-forecast";

  const filteredTickers = useMemo(() => {
    let list = tickers;
    // Hide index/commodity on forecast tabs
    // (keep stocks + ETFs)
    if (isForecastTab) {
      list = list.filter((t) => {
        const tt = tickerTypes[t] ?? "stock";
        return tt === "stock" || tt === "etf";
      });
    }
    if (!tickerSearch.trim()) return list;
    const q = tickerSearch.trim().toUpperCase();
    return list.filter((t) => t.includes(q));
  }, [tickers, tickerSearch, isForecastTab, tickerTypes]);

  const selectTicker = useCallback(
    (t: string) => {
      setSelectedTicker(t);
      setTickerSearch("");
      setShowTickerDropdown(false);
      updatePrefs("chart", { ticker: t });
    },
    [updatePrefs],
  );

  // Auto-redirect away from non-stock tickers on
  // forecast tabs (covers URL-driven navigation).
  useEffect(() => {
    const fc =
      activeTab === "forecast" ||
      activeTab === "portfolio-forecast";
    if (!fc || !selectedTicker) return;
    if (Object.keys(tickerTypes).length === 0) return;
    const tt = tickerTypes[selectedTicker] ?? "stock";
    if (tt === "stock" || tt === "etf") return;
    const first = tickers.find((t) => {
      const tp = tickerTypes[t] ?? "stock";
      return tp === "stock" || tp === "etf";
    });
    if (first && first !== selectedTicker) {
      setSelectedTicker(first);
      setTickerSearch("");
      updatePrefs("chart", { ticker: first });
    }
  }, [
    activeTab,
    selectedTicker,
    tickerTypes,
    tickers,
    updatePrefs,
  ]);

  // Per-ticker refresh (stock analysis tabs)
  type TickerRefreshState =
    | "idle"
    | "pending"
    | "success"
    | "error";
  const [tickerRefresh, setTickerRefresh] =
    useState<TickerRefreshState>("idle");
  const [tickerRefreshKey, setTickerRefreshKey] =
    useState(0);
  const startTickerRefresh = useCallback(
    async () => {
      if (!selectedTicker) return;
      setTickerRefresh("pending");
      try {
        const t = encodeURIComponent(
          selectedTicker,
        );
        const r = await apiFetch(
          `${API_URL}/dashboard/refresh/${t}`,
          { method: "POST" },
        );
        if (!r.ok)
          throw new Error(`HTTP ${r.status}`);
        // Poll for completion
        for (let i = 0; i < 90; i++) {
          await new Promise((ok) =>
            setTimeout(ok, 2000),
          );
          const sr = await apiFetch(
            `${API_URL}/dashboard/refresh/${t}/status`,
          );
          if (!sr.ok) break;
          const s = await sr.json();
          if (s.status === "success") {
            setTickerRefresh("success");
            setTickerRefreshKey((k) => k + 1);
            setTimeout(
              () => setTickerRefresh("idle"),
              3000,
            );
            return;
          }
          if (s.status === "error") {
            setTickerRefresh("error");
            setTimeout(
              () => setTickerRefresh("idle"),
              5000,
            );
            return;
          }
        }
      } catch {
        setTickerRefresh("error");
        setTimeout(
          () => setTickerRefresh("idle"),
          5000,
        );
      }
    },
    [selectedTicker],
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
        {/* Tabs — underline style (matches Insights/Admin) */}
        <div className="flex gap-1 overflow-x-auto border-b border-gray-200 dark:border-gray-700 pb-px">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              data-testid={
                `analytics-tab-${tab.id}`
              }
              onClick={() => {
                setActiveTab(tab.id);
                updatePrefs("chart", { tab: tab.id });
                // Auto-switch ticker if non-stock
                // is selected on a forecast tab
                const isFc =
                  tab.id === "forecast" ||
                  tab.id === "portfolio-forecast";
                if (
                  isFc &&
                  selectedTicker &&
                  !["stock", "etf"].includes(
                    tickerTypes[selectedTicker] ??
                      "stock",
                  )
                ) {
                  const first = tickers.find(
                    (t) =>
                      ["stock", "etf"].includes(
                        tickerTypes[t] ?? "stock",
                      ),
                  );
                  if (first) selectTicker(first);
                }
                const params = new URLSearchParams(
                  searchParams.toString(),
                );
                params.set("tab", tab.id);
                router.replace(
                  `/analytics/analysis?${params}`,
                  { scroll: false },
                );
              }}
              className={`whitespace-nowrap px-3 py-2 text-sm font-medium rounded-t-lg transition-colors ${
                activeTab === tab.id
                  ? "text-indigo-600 dark:text-indigo-400 border-b-2 border-indigo-600 dark:border-indigo-400 -mb-px"
                  : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Searchable ticker + refresh — RIGHT */}
        <div
          className={`relative ${activeTab === "compare" || activeTab === "recommendations" || activeTab.startsWith("portfolio") ? "invisible" : ""}`}
        >
          <div className="flex items-center gap-1.5">
            <button
              onClick={startTickerRefresh}
              disabled={
                tickerRefresh === "pending"
              }
              title={
                tickerRefresh === "pending"
                  ? "Refreshing..."
                  : tickerRefresh === "success"
                    ? "Updated!"
                    : tickerRefresh === "error"
                      ? "Refresh failed"
                      : `Refresh ${selectedTicker} data`
              }
              className="p-1 rounded-md text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-50"
            >
              {tickerRefresh === "pending" ? (
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="50 20" />
                </svg>
              ) : tickerRefresh === "success" ? (
                <svg className="w-3.5 h-3.5 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              ) : tickerRefresh === "error" ? (
                <svg className="w-3.5 h-3.5 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              ) : (
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
                  <path d="M21 3v5h-5" />
                </svg>
              )}
            </button>
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
        <AnalysisTab
          key={`${selectedTicker}-${tickerRefreshKey}`}
          ticker={selectedTicker}
          market={tickerMarkets[selectedTicker]}
          prefs={chartPrefs}
          onPrefsChange={(v) =>
            updatePrefs("chart", v)
          }
        />
      )}
      {activeTab === "forecast" && (
        <ForecastTab
          key={`${selectedTicker}-${tickerRefreshKey}`}
          ticker={selectedTicker}
          market={tickerMarkets[selectedTicker]}
        />
      )}
      {activeTab === "compare" && <CompareTab />}
      {activeTab === "recommendations" && (
        <RecommendationHistoryTab />
      )}
      {activeTab === "portfolio" && (
        <PortfolioTab
          marketFilter={
            (chartPrefs.marketFilter as string)
            ?? (userPrefs.dashboard as Record<string, unknown>)?.marketFilter as string
            ?? "india"
          }
        />
      )}
      {activeTab === "portfolio-forecast" && (
        <PortfolioForecastTab
          marketFilter={
            (chartPrefs.marketFilter as string)
            ?? (userPrefs.dashboard as Record<string, unknown>)?.marketFilter as string
            ?? "india"
          }
        />
      )}
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
