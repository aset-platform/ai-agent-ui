"use client";

import { useState } from "react";
import type { DashboardData } from "@/hooks/useDashboardData";
import type { LLMUsageResponse, ModelUsage } from "@/lib/types";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";

interface LLMUsageWidgetProps {
  data: DashboardData<LLMUsageResponse>;
}

function formatCost(value: number): string {
  return `$${value.toFixed(2)}`;
}

function formatLatency(value: number | null): string {
  if (value === null) return "--";
  return `${Math.round(value)}ms`;
}

/** Shorten long model names for display. */
function shortName(model: string): string {
  const map: Record<string, string> = {
    "llama-3.3-70b-versatile": "Llama 3.3 70B",
    "llama-3.1-8b-instant": "Llama 3.1 8B",
    "llama3-70b-8192": "Llama 3 70B",
    "gemma2-9b-it": "Gemma 2 9B",
    "claude-sonnet-4-20250514": "Claude Sonnet",
    "claude-haiku-4-5-20251001": "Claude Haiku",
    "openai/gpt-oss-120b": "GPT OSS 120B",
    "moonshotai/kimi-k2-instruct": "Kimi K2",
    "test-model": "Test",
  };
  return map[model] ?? model.split("/").pop()
    ?.replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    ?? model;
}

const DONUT_COLORS = [
  "#6366f1", // indigo-500
  "#3b82f6", // blue-500
  "#8b5cf6", // violet-500
  "#06b6d4", // cyan-500
  "#10b981", // emerald-500
  "#f59e0b", // amber-500
];

function DonutChart({
  models,
  totalRequests,
}: {
  models: ModelUsage[];
  totalRequests: number;
}) {
  const [hovered, setHovered] = useState<number | null>(null);

  if (models.length === 0 || totalRequests === 0)
    return null;

  const cx = 50;
  const cy = 50;
  const r = 35;
  const circumference = 2 * Math.PI * r;
  let offset = 0;

  const segments = models.map((m, i) => {
    const pct = m.request_count / totalRequests;
    const dashLen = pct * circumference;
    const seg = {
      model: m,
      color: DONUT_COLORS[i % DONUT_COLORS.length],
      dasharray: `${dashLen} ${circumference - dashLen}`,
      dashoffset: -offset,
      pct,
      idx: i,
    };
    offset += dashLen;
    return seg;
  });

  const active = hovered !== null ? segments[hovered] : null;

  return (
    <div className="flex items-start gap-5">
      {/* Donut with HTML tooltip above */}
      <div className="relative shrink-0">
        {/* Tooltip — positioned above the donut */}
        {active && (
          <div
            className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 z-10 pointer-events-none"
          >
            <div
              className="rounded-xl px-3.5 py-2.5 shadow-2xl whitespace-nowrap backdrop-blur-sm bg-white/95 dark:bg-gray-800/95 border border-gray-200 dark:border-gray-600"
            >
              <div className="flex items-center gap-2 mb-1">
                <span
                  className="h-2.5 w-2.5 rounded-full shrink-0"
                  style={{ backgroundColor: active.color }}
                />
                <p
                  className="text-gray-900 dark:text-white text-xs font-semibold"
                  style={{ fontFamily: "'DM Sans', sans-serif" }}
                >
                  {shortName(active.model.model)}
                </p>
              </div>
              <p
                className="text-gray-500 dark:text-gray-400 text-[11px]"
                style={{ fontFamily: "'IBM Plex Mono', monospace" }}
              >
                {active.model.request_count} req &middot;{" "}
                <span
                  className="font-bold"
                  style={{ color: active.color }}
                >
                  {Math.round(active.pct * 100)}%
                </span>
              </p>
            </div>
          </div>
        )}

        <svg
          width={100}
          height={100}
          viewBox="0 0 100 100"
        >
          {/* Base ring */}
          <circle
            cx={cx} cy={cy} r={r}
            fill="none"
            stroke="currentColor"
            strokeOpacity={0.06}
            strokeWidth={12}
          />
          {/* Segments */}
          {segments.map((seg) => (
            <circle
              key={seg.model.model}
              cx={cx} cy={cy} r={r}
              fill="none"
              stroke={seg.color}
              strokeWidth={hovered === seg.idx ? 15 : 12}
              strokeDasharray={seg.dasharray}
              strokeDashoffset={seg.dashoffset}
              transform={`rotate(-90 ${cx} ${cy})`}
              strokeLinecap="round"
              className="transition-all duration-150 cursor-pointer"
              onMouseEnter={() => setHovered(seg.idx)}
              onMouseLeave={() => setHovered(null)}
            />
          ))}
          {/* Center label */}
          <text
            x={cx} y={cy + 4}
            textAnchor="middle"
            className="fill-gray-700 dark:fill-gray-300"
            style={{
              fontSize: "12px",
              fontWeight: 600,
              fontFamily: "'IBM Plex Mono', monospace",
            }}
          >
            {totalRequests >= 1000
              ? `${(totalRequests / 1000).toFixed(1)}k`
              : totalRequests}
          </text>
        </svg>
      </div>

      {/* Legend */}
      <div className="flex flex-col gap-1.5 min-w-0 pt-2">
        {segments.map((seg) => (
          <div
            key={seg.model.model}
            className={`flex items-center gap-2 px-1.5 py-0.5 rounded transition-colors duration-150 cursor-default ${
              hovered === seg.idx
                ? "bg-gray-100 dark:bg-gray-800"
                : ""
            }`}
            onMouseEnter={() => setHovered(seg.idx)}
            onMouseLeave={() => setHovered(null)}
          >
            <span
              className="h-2.5 w-2.5 rounded-full shrink-0"
              style={{ backgroundColor: seg.color }}
            />
            <span className="text-xs text-gray-700 dark:text-gray-300 truncate">
              {shortName(seg.model.model)}
            </span>
            <span className="text-xs font-mono text-gray-400 dark:text-gray-500">
              {Math.round(seg.pct * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ModelBar({
  model,
  maxRequests,
  isPrimary,
}: {
  model: ModelUsage;
  maxRequests: number;
  isPrimary: boolean;
}) {
  const pct =
    maxRequests > 0
      ? (model.request_count / maxRequests) * 100
      : 0;

  return (
    <div className="py-2">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="
              text-sm font-medium text-gray-900
              dark:text-gray-100 truncate
            "
          >
            {shortName(model.model)}
          </span>
          <span
            className="
              inline-flex items-center px-1.5 py-0.5
              rounded text-[10px] font-medium uppercase
              tracking-wider
              bg-gray-100 text-gray-500
              dark:bg-gray-800 dark:text-gray-400
            "
          >
            {model.provider}
          </span>
        </div>
        <div
          className="
            flex items-center gap-3 shrink-0
            text-xs text-gray-500 dark:text-gray-400
          "
        >
          <span className="font-mono">
            {model.request_count.toLocaleString()} req
          </span>
          <span className="font-mono">
            {formatCost(model.estimated_cost_usd)}
          </span>
        </div>
      </div>

      {/* Percentage bar */}
      <div
        className="
          h-2 w-full rounded-full overflow-hidden
          bg-gray-100 dark:bg-gray-800
        "
      >
        <div
          className={`
            h-full rounded-full transition-all duration-500
            ${
              isPrimary
                ? "bg-gradient-to-r from-blue-500 to-indigo-500"
                : "bg-gray-300 dark:bg-gray-600"
            }
          `}
          style={{ width: `${Math.max(pct, 1)}%` }}
        />
      </div>
    </div>
  );
}

export function LLMUsageWidget({
  data,
}: LLMUsageWidgetProps) {
  if (data.loading) {
    return <WidgetSkeleton className="h-64" />;
  }

  if (data.error) {
    return <WidgetError message={data.error} />;
  }

  const usage = data.value;

  if (!usage) {
    return (
      <div
        className="
          rounded-xl border border-gray-200
          dark:border-gray-700
          bg-white dark:bg-gray-900
          shadow-sm px-5 py-10 text-center
        "
      >
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No usage data
        </p>
      </div>
    );
  }

  const maxRequests = Math.max(
    ...usage.models.map((m) => m.request_count),
    1,
  );

  return (
    <div
      className="
        rounded-xl border border-gray-200
        dark:border-gray-700
        bg-white dark:bg-gray-900
        shadow-sm
      "
    >
      {/* Header */}
      <div
        className="
          px-5 py-4 border-b border-gray-100
          dark:border-gray-800
        "
      >
        <div className="flex items-baseline gap-2">
          <h3
            className="
              text-base font-semibold text-gray-900
              dark:text-gray-100
            "
          >
            LLM Usage
          </h3>
          <span
            className="
              text-xs text-gray-400 dark:text-gray-500
            "
          >
            (30 days)
          </span>
        </div>
      </div>

      {/* Stats grid */}
      <div
        className="
          grid grid-cols-3 gap-4 px-5 py-4
          border-b border-gray-100 dark:border-gray-800
        "
      >
        <div>
          <p
            className="
              text-xs text-gray-500 dark:text-gray-400
              mb-1
            "
          >
            Total Requests
          </p>
          <p
            className="
              font-mono text-xl font-semibold
              text-gray-900 dark:text-gray-100
            "
          >
            {usage.total_requests.toLocaleString()}
          </p>
        </div>
        <div>
          <p
            className="
              text-xs text-gray-500 dark:text-gray-400
              mb-1
            "
          >
            Total Cost
          </p>
          <p
            className="
              font-mono text-xl font-semibold
              text-gray-900 dark:text-gray-100
            "
          >
            {formatCost(usage.total_cost_usd)}
          </p>
        </div>
        <div>
          <p
            className="
              text-xs text-gray-500 dark:text-gray-400
              mb-1
            "
          >
            Avg Latency
          </p>
          <p
            className="
              font-mono text-xl font-semibold
              text-gray-900 dark:text-gray-100
            "
          >
            {formatLatency(usage.avg_latency_ms)}
          </p>
        </div>
      </div>

      {/* Donut chart */}
      {usage.models.length > 0 && (
        <div
          className="
            px-5 py-4 border-b border-gray-100
            dark:border-gray-800
          "
        >
          <DonutChart
            models={usage.models}
            totalRequests={usage.total_requests}
          />
        </div>
      )}

      {/* Model breakdown */}
      <div className="px-5 py-4">
        <p
          className="
            text-xs font-medium uppercase tracking-wider
            text-gray-400 dark:text-gray-500 mb-2
          "
        >
          Model Breakdown
        </p>
        {usage.models.length === 0 ? (
          <p
            className="
              text-sm text-gray-500 dark:text-gray-400
              text-center py-4
            "
          >
            No model data
          </p>
        ) : (
          <div className="space-y-1">
            {usage.models.map((model, i) => (
              <ModelBar
                key={model.model}
                model={model}
                maxRequests={maxRequests}
                isPrimary={i === 0}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
