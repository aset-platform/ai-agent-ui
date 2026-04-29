"use client";

import { useEffect, useMemo, useState } from "react";
import type { DashboardData } from "@/hooks/useDashboardData";
import type {
  ForecastsResponse,
  TickerForecast,
  ForecastTarget,
  ForecastConfidence,
} from "@/lib/types";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";

import type { MarketFilter } from "@/app/(authenticated)/dashboard/DashboardClient";

interface ForecastChartWidgetProps {
  data: DashboardData<ForecastsResponse>;
  marketFilter?: MarketFilter;
  selectedTicker?: string | null;
}

function horizonLabel(months: number): string {
  return `${months}-month`;
}

function formatPrice(
  value: number,
  sym: string = "$",
): string {
  return `${sym}${value.toFixed(2)}`;
}

function formatPctChange(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function HorizonCard({ target, sym }: { target: ForecastTarget; sym: string }) {
  const isPositive = target.pct_change >= 0;

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
        {horizonLabel(target.horizon_months)}
      </p>
      <p
        className="
          text-xs text-gray-500 dark:text-gray-400
          mb-2
        "
      >
        {target.target_date}
      </p>

      {/* Target price */}
      <p
        className="
          font-mono text-2xl font-semibold
          text-gray-900 dark:text-gray-100 mb-1
        "
      >
        {formatPrice(target.target_price, sym)}
      </p>

      {/* Percent change pill */}
      <span
        className={`
          inline-flex items-center px-2 py-0.5
          rounded-full text-xs font-medium
          ${
            isPositive
              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
              : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
          }
        `}
      >
        {formatPctChange(target.pct_change)}
      </span>

      {/* Confidence range */}
      <p
        className="
          font-mono text-xs text-gray-400
          dark:text-gray-500 mt-2
        "
      >
        {formatPrice(target.lower_bound, sym)}
        {" \u2014 "}
        {formatPrice(target.upper_bound, sym)}
      </p>
    </div>
  );
}

function ForecastSVGChart({
  forecast,
  sym,
}: {
  forecast: TickerForecast;
  sym: string;
}) {
  const targets = forecast.targets;
  if (targets.length === 0) return null;

  const W = 700;
  const H = 240;
  const PAD = {
    top: 30, right: 30, bottom: 36, left: 65,
  };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top - PAD.bottom;

  const current = forecast.current_price;
  const allPrices = [
    current,
    ...targets.map((t) => t.target_price),
    ...targets.map((t) => t.upper_bound),
    ...targets.map((t) => t.lower_bound),
  ];
  const minP = Math.min(...allPrices) * 0.96;
  const maxP = Math.max(...allPrices) * 1.04;
  const priceRange = maxP - minP || 1;

  const dataPoints = [
    {
      m: 0,
      p: current,
      date: "Current",
      upper: current,
      lower: current,
    },
    ...targets.map((t) => ({
      m: t.horizon_months,
      p: t.target_price,
      date: t.target_date,
      upper: t.upper_bound,
      lower: t.lower_bound,
    })),
  ];
  const maxMonth = Math.max(
    ...dataPoints.map((d) => d.m),
  );

  const xScale = (m: number) =>
    PAD.left + (m / maxMonth) * chartW;
  const yScale = (p: number) =>
    PAD.top + ((maxP - p) / priceRange) * chartH;

  // Smooth curve helper (monotone cubic)
  const toPath = (
    pts: { x: number; y: number }[],
  ) => {
    if (pts.length < 2)
      return `M${pts[0].x},${pts[0].y}`;
    let d = `M${pts[0].x},${pts[0].y}`;
    for (let i = 1; i < pts.length; i++) {
      const cx =
        (pts[i - 1].x + pts[i].x) / 2;
      d += ` C${cx},${pts[i - 1].y} ${cx},${pts[i].y} ${pts[i].x},${pts[i].y}`;
    }
    return d;
  };

  const forecastPts = dataPoints.map((d) => ({
    x: xScale(d.m),
    y: yScale(d.p),
  }));
  const upperPts = dataPoints.map((d) => ({
    x: xScale(d.m),
    y: yScale(d.upper),
  }));
  const lowerPts = dataPoints.map((d) => ({
    x: xScale(d.m),
    y: yScale(d.lower),
  }));

  // Band polygon
  const bandPath =
    upperPts.map(
      (p, i) =>
        `${i === 0 ? "M" : "L"}${p.x},${p.y}`,
    ).join(" ") +
    " " +
    [...lowerPts].reverse().map(
      (p) => `L${p.x},${p.y}`,
    ).join(" ") +
    " Z";

  const yTicks = Array.from(
    { length: 5 },
    (_, i) => minP + (priceRange * i) / 4,
  );

  return (
    <div className="mb-4">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <linearGradient
            id="fcBand"
            x1="0" y1="0" x2="0" y2="1"
          >
            <stop
              offset="0%"
              stopColor="#7c3aed"
              stopOpacity="0.18"
            />
            <stop
              offset="100%"
              stopColor="#7c3aed"
              stopOpacity="0.02"
            />
          </linearGradient>
          <filter id="glow">
            <feGaussianBlur
              stdDeviation="2"
              result="blur"
            />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Grid */}
        {yTicks.map((p) => (
          <line
            key={p}
            x1={PAD.left}
            y1={yScale(p)}
            x2={W - PAD.right}
            y2={yScale(p)}
            stroke="currentColor"
            strokeOpacity={0.06}
            strokeWidth={1}
          />
        ))}

        {/* Y labels */}
        {yTicks.map((p) => (
          <text
            key={`y-${p}`}
            x={PAD.left - 8}
            y={yScale(p) + 3}
            textAnchor="end"
            className="fill-gray-400 dark:fill-gray-500"
            style={{
              fontSize: "9px",
              fontFamily:
                "'IBM Plex Mono', monospace",
            }}
          >
            {sym}{p.toFixed(0)}
          </text>
        ))}

        {/* X labels with dates */}
        {dataPoints.map((d) => (
          <text
            key={`x-${d.m}`}
            x={xScale(d.m)}
            y={H - 8}
            textAnchor="middle"
            className="fill-gray-400 dark:fill-gray-500"
            style={{
              fontSize: "9px",
              fontFamily:
                "'IBM Plex Mono', monospace",
            }}
          >
            {d.m === 0 ? "Now" : `${d.m}m`}
          </text>
        ))}

        {/* Reference line */}
        <line
          x1={PAD.left}
          y1={yScale(current)}
          x2={W - PAD.right}
          y2={yScale(current)}
          stroke="#7c3aed"
          strokeWidth={0.8}
          strokeDasharray="3 3"
          strokeOpacity={0.3}
        />

        {/* Confidence band */}
        <path
          d={bandPath}
          fill="url(#fcBand)"
        />

        {/* Upper/lower bound lines */}
        <path
          d={toPath(upperPts)}
          fill="none"
          stroke="#7c3aed"
          strokeWidth={1}
          strokeOpacity={0.2}
          strokeDasharray="2 2"
        />
        <path
          d={toPath(lowerPts)}
          fill="none"
          stroke="#7c3aed"
          strokeWidth={1}
          strokeOpacity={0.2}
          strokeDasharray="2 2"
        />

        {/* Forecast line (smooth) */}
        <path
          d={toPath(forecastPts)}
          fill="none"
          stroke="#7c3aed"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          filter="url(#glow)"
        />

        {/* Interactive data points with tooltips */}
        {dataPoints.map((d, i) => (
          <g key={i} className="group">
            {/* Invisible hit area */}
            <circle
              cx={xScale(d.m)}
              cy={yScale(d.p)}
              r={16}
              fill="transparent"
              className="cursor-pointer"
            />
            {/* Visible dot */}
            <circle
              cx={xScale(d.m)}
              cy={yScale(d.p)}
              r={i === 0 ? 5 : 6}
              fill={i === 0 ? "#7c3aed" : "white"}
              stroke="#7c3aed"
              strokeWidth={2.5}
              className="transition-all duration-150 group-hover:r-[8] group-hover:drop-shadow-lg"
            />
            {/* Tooltip (hidden, shown on hover) */}
            <g
              className="opacity-0 group-hover:opacity-100 transition-opacity duration-200"
              style={{ pointerEvents: "none" }}
            >
              {/* Shadow */}
              <rect
                x={xScale(d.m) - 58}
                y={yScale(d.p) - 50}
                width={116}
                height={40}
                rx={8}
                className="fill-black/10 dark:fill-black/30"
                transform="translate(1,1)"
              />
              {/* Background */}
              <rect
                x={xScale(d.m) - 58}
                y={yScale(d.p) - 50}
                width={116}
                height={40}
                rx={8}
                className="fill-white dark:fill-gray-800"
              />
              {/* Border */}
              <rect
                x={xScale(d.m) - 58}
                y={yScale(d.p) - 50}
                width={116}
                height={40}
                rx={8}
                fill="none"
                className="stroke-gray-200 dark:stroke-gray-600"
                strokeWidth={1}
              />
              {/* Color accent */}
              <rect
                x={xScale(d.m) - 58}
                y={yScale(d.p) - 50}
                width={3}
                height={40}
                rx={1.5}
                fill="#7c3aed"
              />
              {/* Date */}
              <text
                x={xScale(d.m)}
                y={yScale(d.p) - 35}
                textAnchor="middle"
                className="fill-gray-500 dark:fill-gray-400"
                style={{
                  fontSize: "9px",
                  fontFamily:
                    "'IBM Plex Mono', monospace",
                }}
              >
                {d.date}
              </text>
              {/* Price */}
              <text
                x={xScale(d.m)}
                y={yScale(d.p) - 20}
                textAnchor="middle"
                className="fill-gray-900 dark:fill-white"
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  fontFamily:
                    "'IBM Plex Mono', monospace",
                }}
              >
                {sym}{d.p.toFixed(2)}
              </text>
            </g>
          </g>
        ))}
      </svg>
    </div>
  );
}

function ForecastDetail({
  forecast,
  sym,
}: {
  forecast: TickerForecast;
  sym: string;
}) {
  return (
    <div>
      {/* SVG forecast chart */}
      <ForecastSVGChart forecast={forecast} sym={sym} />

      {/* Horizon cards — hide if any target is extreme */}
      {forecast.targets.some(
        (t) => Math.abs(t.pct_change) > 200,
      ) ? (
        <div
          className="
            p-4 rounded-lg border mb-4
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
            unreliable. The model struggles with highly
            volatile price histories.
          </p>
        </div>
      ) : (
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        {forecast.targets.map((target) => (
          <HorizonCard
            key={target.horizon_months}
            target={target}
            sym={sym}
          />
        ))}
      </div>
      )}

      {/* Accuracy metrics */}
      {(forecast.mae != null || forecast.rmse != null || forecast.mape != null) && (
        <div
          className="
            flex items-center gap-6
            pt-3 border-t border-gray-100
            dark:border-gray-800
          "
        >
          {forecast.mae != null && (
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-gray-400 dark:text-gray-500">
                MAE
              </span>
              <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                {forecast.mae.toFixed(2)}
              </span>
            </div>
          )}
          {forecast.rmse != null && (
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-gray-400 dark:text-gray-500">
                RMSE
              </span>
              <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                {forecast.rmse.toFixed(2)}
              </span>
            </div>
          )}
          {forecast.mape != null && (
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-gray-400 dark:text-gray-500">
                MAPE
              </span>
              <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                {Number(forecast.mape).toFixed(2)}%
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ForecastChartWidget({
  data,
  marketFilter,
  selectedTicker,
}: ForecastChartWidgetProps) {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [showConfidence, setShowConfidence] = useState(false);
  const sym = marketFilter === "india" ? "₹" : "$";
  const forecasts = useMemo(
    () => data.value?.forecasts ?? [],
    [data.value],
  );

  // Auto-select forecast matching selectedTicker
  // (must be before early returns — Rules of Hooks).
  // Defer past the synchronous effect body so the rule
  // treats setSelectedIdx as an async-callback update.
  useEffect(() => {
    if (!selectedTicker || forecasts.length === 0)
      return;
    let alive = true;
    void Promise.resolve().then(() => {
      if (!alive) return;
      const matchIdx = forecasts.findIndex(
        (f) =>
          f.ticker.toUpperCase() ===
          selectedTicker.toUpperCase(),
      );
      if (matchIdx >= 0) setSelectedIdx(matchIdx);
    });
    return () => {
      alive = false;
    };
  }, [selectedTicker, forecasts]);

  if (data.loading) {
    return <WidgetSkeleton className="h-64" />;
  }

  if (data.error) {
    return <WidgetError message={data.error} />;
  }

  if (forecasts.length === 0) {
    return (
      <div
        className="
          col-span-full rounded-xl border
          border-gray-200 dark:border-gray-700
          bg-white dark:bg-gray-900
          shadow-sm px-5 py-10 text-center
        "
      >
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No forecast data yet
        </p>
      </div>
    );
  }

  const selected = forecasts[selectedIdx] ?? forecasts[0];

  const confidence: ForecastConfidence | null = (() => {
    const cc = (selected as unknown as Record<string, unknown>)?.confidence_components;
    if (!cc) return null;
    const parsed = typeof cc === "string" ? JSON.parse(cc) : cc;
    const cs = (selected as unknown as Record<string, unknown>)?.confidence_score;
    return {
      score: typeof cs === "number" ? cs : 0,
      badge: parsed.badge ?? "Medium",
      reason: parsed.reason ?? "",
      direction: parsed.direction ?? 0,
      mase: parsed.mase ?? 0,
      coverage: parsed.coverage ?? 0,
      interval: parsed.interval ?? 0,
      data_completeness: parsed.data_completeness ?? 0,
      regime: parsed.regime ?? "moderate",
    };
  })();

  return (
    <div
      data-testid="dashboard-forecast-widget"
      className="
        col-span-full rounded-xl border
        border-gray-200 dark:border-gray-700
        bg-white dark:bg-gray-900
        shadow-sm
      "
    >
      {/* Header + ticker selector */}
      <div
        className="
          px-5 py-4 border-b border-gray-100
          dark:border-gray-800
          flex items-center justify-between
        "
      >
        <div className="flex items-baseline gap-2">
          <h3
            className="
              text-base font-semibold text-gray-900
              dark:text-gray-100
            "
          >
            Forecast
          </h3>
          {selected && (
            <span
              className="
                text-xs text-gray-400
                dark:text-gray-500
              "
            >
              as of {selected.run_date}
            </span>
          )}
        </div>

        {/* Ticker selector (only if multiple) */}
        {forecasts.length > 1 && (
          <select
            value={selectedIdx}
            onChange={(e) =>
              setSelectedIdx(Number(e.target.value))
            }
            className="
              text-sm rounded-md px-2.5 py-1.5
              border border-gray-200 dark:border-gray-700
              bg-white dark:bg-gray-800
              text-gray-900 dark:text-gray-100
              focus:outline-none focus:ring-2
              focus:ring-blue-500/40
            "
          >
            {forecasts.map((f, i) => (
              <option key={f.ticker} value={i}>
                {f.ticker}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Content */}
      <div className="px-5 py-4">
        {/* Current price */}
        {selected && (
          <div className="mb-4">
            <span
              className="
                text-xs text-gray-400
                dark:text-gray-500
              "
            >
              Current Price
            </span>
            <p
              className="
                font-mono text-lg font-semibold
                text-gray-900 dark:text-gray-100
              "
            >
              {formatPrice(
                selected.latest_close ?? selected.current_price,
                sym,
              )}
              {selected.latest_close != null
                && selected.latest_close !== selected.current_price && (
                <span
                  className="
                    ml-2 text-xs font-normal
                    text-gray-400 dark:text-gray-500
                  "
                >
                  (anchored at {formatPrice(selected.current_price, sym)}
                  {selected.run_date ? ` on ${selected.run_date}` : ""})
                </span>
              )}
              {selected.sentiment && (
                <span
                  className="
                    ml-2 text-xs font-normal
                    text-gray-400 dark:text-gray-500
                  "
                >
                  Sentiment: {selected.sentiment}
                </span>
              )}
              {/* Confidence Badge */}
              {confidence && confidence.badge !== "Rejected" && (
                <span className="relative inline-block ml-3">
                  <button
                    type="button"
                    className={`
                      inline-flex items-center px-2 py-0.5 rounded-full
                      text-xs font-medium
                      ${confidence.badge === "High"
                        ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : confidence.badge === "Medium"
                        ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400"
                        : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                      }
                    `}
                    onClick={() => setShowConfidence(!showConfidence)}
                  >
                    {confidence.badge} Confidence
                  </button>
                  {showConfidence && (
                    <span className="absolute z-10 mt-1 w-64 p-3 bg-white dark:bg-gray-800
                      border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg text-xs block">
                      <span className="space-y-1.5 block">
                        <span className="flex justify-between">
                          <span className="text-gray-500">Directional accuracy</span>
                          <span>{(confidence.direction * 100).toFixed(0)}%</span>
                        </span>
                        <span className="flex justify-between">
                          <span className="text-gray-500">Forecast error (MASE)</span>
                          <span>{confidence.mase.toFixed(2)}</span>
                        </span>
                        <span className="flex justify-between">
                          <span className="text-gray-500">Interval coverage</span>
                          <span>{(confidence.coverage * 100).toFixed(0)}%</span>
                        </span>
                        <span className="flex justify-between">
                          <span className="text-gray-500">Data signals</span>
                          <span>{(confidence.data_completeness * 14).toFixed(0)} of 14</span>
                        </span>
                        {confidence.reason && (
                          <span className="text-gray-400 pt-1 border-t border-gray-200 dark:border-gray-700 block">
                            {confidence.reason}
                          </span>
                        )}
                      </span>
                    </span>
                  )}
                </span>
              )}
            </p>
          </div>
        )}

        <ForecastDetail forecast={selected} sym={sym} />

        {confidence && confidence.badge === "Rejected" && (
          <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200
            dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-400">
            Forecast unavailable — insufficient model confidence.
            {confidence.reason && (
              <span className="block mt-1 text-xs">{confidence.reason}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
