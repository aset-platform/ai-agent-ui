"use client";
/**
 * Individual recommendation card with tier badge, severity
 * border, signal pills, and a link to the analysis page.
 */

import Link from "next/link";
import type { RecommendationItem } from "@/lib/types";
import { SignalPill } from "./SignalPill";
import { TierBadge } from
  "@/components/recommendations/badges";
import {
  RecActionButton,
} from "@/components/recommendations/RecActionButton";

interface RecommendationCardProps {
  rec: RecommendationItem;
  expanded?: boolean;
  onActionClick?: () => void;
}

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
  expanded = false,
  onActionClick,
}: RecommendationCardProps) {
  const border =
    severityBorder[rec.severity] ?? severityBorder.low;

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
        <TierBadge tier={rec.tier} />
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
      <p
        className={
          "text-sm text-gray-600 " +
          "dark:text-gray-400 leading-relaxed" +
          (expanded ? "" : " line-clamp-3")
        }
      >
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

      {/* Expected impact + actions */}
      <div className="flex items-center justify-between pt-1 gap-2">
        {rec.expected_impact && (
          <span className="text-xs text-gray-500 dark:text-gray-400 italic truncate">
            {rec.expected_impact}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {rec.ticker && (
            <RecActionButton
              ticker={rec.ticker}
              action={rec.action}
              actedOn={!!rec.acted_on_date}
              onBeforeNavigate={onActionClick}
            />
          )}
          {rec.ticker && (
            <Link
              href={
                `/analytics/analysis?ticker=${rec.ticker}`
              }
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
            >
              View &rarr;
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
