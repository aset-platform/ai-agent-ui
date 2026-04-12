"use client";
/**
 * Individual recommendation card with tier badge, severity
 * border, signal pills, and a link to the analysis page.
 */

import Link from "next/link";
import type { RecommendationItem } from "@/lib/types";
import { SignalPill } from "./SignalPill";

interface RecommendationCardProps {
  rec: RecommendationItem;
}

/* ── Tier badge colors ─────────────────────────────── */
const tierStyles: Record<string, string> = {
  portfolio:
    "bg-blue-100 text-blue-700 " +
    "dark:bg-blue-900/30 dark:text-blue-400",
  watchlist:
    "bg-emerald-100 text-emerald-700 " +
    "dark:bg-emerald-900/30 dark:text-emerald-400",
  discovery:
    "bg-purple-100 text-purple-700 " +
    "dark:bg-purple-900/30 dark:text-purple-400",
};

/* ── Severity left-border ──────────────────────────── */
const severityBorder: Record<string, string> = {
  high: "border-l-red-500",
  medium: "border-l-amber-500",
  low: "border-l-blue-400",
};

/* ── Signal quality heuristic ──────────────────────── */
function signalQuality(
  key: string,
  val: number | string,
): "good" | "neutral" | "bad" {
  if (typeof val !== "number") return "neutral";
  const k = key.toLowerCase();
  if (k.includes("piotroski") || k.includes("f_score")) {
    return val >= 7 ? "good" : val >= 4 ? "neutral" : "bad";
  }
  if (k.includes("sharpe")) {
    return val >= 1 ? "good" : val >= 0 ? "neutral" : "bad";
  }
  if (k.includes("sentiment")) {
    return val >= 0.6
      ? "good"
      : val >= 0.3
        ? "neutral"
        : "bad";
  }
  if (k.includes("forecast") || k.includes("return")) {
    return val > 0 ? "good" : val === 0 ? "neutral" : "bad";
  }
  return "neutral";
}

function labelFromKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function RecommendationCard({
  rec,
}: RecommendationCardProps) {
  const border =
    severityBorder[rec.severity] ?? severityBorder.low;
  const tierCls =
    tierStyles[rec.tier] ?? tierStyles.discovery;

  const signals = Object.entries(rec.data_signals ?? {});

  return (
    <div
      className={
        "border-l-4 rounded-lg bg-white dark:bg-gray-900 " +
        "border border-gray-200 dark:border-gray-700 " +
        "shadow-sm p-4 space-y-2 " +
        border
      }
    >
      {/* Top row: tier + category + ticker */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={
            "text-[10px] font-semibold uppercase " +
            "rounded-full px-2 py-0.5 " +
            tierCls
          }
        >
          {rec.tier}
        </span>
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {rec.category}
        </span>
        {rec.ticker && (
          <span className="ml-auto text-xs font-mono font-semibold text-gray-900 dark:text-gray-100">
            {rec.ticker}
          </span>
        )}
      </div>

      {/* Company name + price */}
      {(rec.company_name || rec.price_at_rec) && (
        <div className="flex items-baseline gap-2">
          {rec.company_name && (
            <span className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
              {rec.company_name}
            </span>
          )}
          {rec.price_at_rec != null && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {"\u20B9"}{rec.price_at_rec.toLocaleString()}
            </span>
          )}
        </div>
      )}

      {/* Rationale */}
      <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-3">
        {rec.rationale}
      </p>

      {/* Signal pills */}
      {signals.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {signals.map(([key, val]) => (
            <SignalPill
              key={key}
              label={labelFromKey(key)}
              value={val}
              quality={signalQuality(key, val)}
            />
          ))}
        </div>
      )}

      {/* Expected impact + View link */}
      <div className="flex items-center justify-between pt-1">
        {rec.expected_impact && (
          <span className="text-xs text-gray-500 dark:text-gray-400 italic">
            {rec.expected_impact}
          </span>
        )}
        {rec.ticker && (
          <Link
            href={
              `/analytics/analysis?ticker=${rec.ticker}`
            }
            className="text-xs font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          >
            View &rarr;
          </Link>
        )}
      </div>
    </div>
  );
}
