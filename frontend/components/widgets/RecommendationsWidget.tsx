"use client";
/**
 * W5: Compact Recommendations Summary (ASETPLTFRM-298).
 *
 * Compact widget showing health score + top 3 preview
 * rows. "View All" opens a slide-over panel with full
 * recommendation list, filters, and signal pills.
 */

import { useState, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";
import { HealthScoreBadge } from "./HealthScoreBadge";
import { RecommendationSlideOver } from "./RecommendationSlideOver";
import type { RecommendationResponse } from "@/lib/types";
import type { KeyedMutator } from "swr";

/* ── Props ─────────────────────────────────────────── */

interface Props {
  data: {
    value: RecommendationResponse | null;
    loading: boolean;
    error: string | null;
    mutate?: KeyedMutator<RecommendationResponse>;
  };
  market?: string;
}

/* ── Severity colors ───────────────────────────────── */

const SEV_DOT: Record<string, string> = {
  high: "bg-red-500",
  medium: "bg-amber-500",
  low: "bg-blue-400",
};

const SEV_ICON: Record<string, string> = {
  high: "text-red-500",
  medium: "text-amber-500",
  low: "text-blue-400",
};

/* ── Category display ──────────────────────────────── */

function categoryLabel(cat: string): string {
  return (cat || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ── Widget ────────────────────────────────────────── */

export function RecommendationsWidget({
  data,
  market = "india",
}: Props) {
  const [open, setOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await apiFetch(
        `${API_URL}/dashboard/portfolio/recommendations/refresh?market=${market}`,
        { method: "POST" },
      );
      data.mutate?.();
    } catch {
      // swallow — mutate will pick up stale data
    } finally {
      setRefreshing(false);
    }
  }, [data, market]);

  /* ── Loading / error ──────────────────────────── */
  if (data.loading)
    return <WidgetSkeleton className="h-72" />;
  if (data.error)
    return <WidgetError message={data.error} />;

  const resp = data.value;
  const recs = resp?.recommendations ?? [];
  const preview = recs.slice(0, 3);
  const totalCount = recs.length;
  const cached = resp?.cached === true;
  const resetAt = resp?.reset_at ?? null;
  const resetLabel = resetAt
    ? new Date(resetAt).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : null;
  const runDate = resp?.run_date
    ? new Date(resp.run_date).toLocaleDateString(
        undefined,
        { month: "short", year: "numeric" },
      )
    : null;

  return (
    <>
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm flex flex-col">
        {/* ── Header ─────────────────────────────── */}
        <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              Recommendations
            </h3>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing || cached}
              title={
                cached && resetLabel
                  ? `Already generated for this month. Next set available ${resetLabel}.`
                  : "Generate this month's recommendations"
              }
              className={
                "inline-flex items-center gap-1.5 " +
                "rounded-lg px-3 py-1.5 text-xs " +
                "font-medium border border-gray-200 " +
                "dark:border-gray-700 bg-white " +
                "dark:bg-gray-800 text-gray-700 " +
                "dark:text-gray-300 hover:bg-gray-50 " +
                "dark:hover:bg-gray-700 " +
                "disabled:opacity-50 transition-colors"
              }
            >
              <svg
                className={
                  "w-3.5 h-3.5" +
                  (refreshing ? " animate-spin" : "")
                }
                viewBox="0 0 20 20"
                fill="currentColor"
              >
                <path
                  fillRule="evenodd"
                  d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311V15a.75.75 0 01-1.5 0v-3.5a.75.75 0 01.75-.75H8.5a.75.75 0 010 1.5H7.058l.166.166a4 4 0 006.66-1.795.75.75 0 011.428.453zm-10.624-2.85a5.5 5.5 0 019.201-2.465l.312.31V5a.75.75 0 011.5 0v3.5a.75.75 0 01-.75.75H11.5a.75.75 0 010-1.5h1.442l-.166-.166a4 4 0 00-6.66 1.795.75.75 0 11-1.428-.453z"
                  clipRule="evenodd"
                />
              </svg>
              {refreshing
                ? "Generating..."
                : cached
                  ? "Generated"
                  : "Generate"}
            </button>
          </div>
          {(cached || resetLabel) && (
            <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
              {cached && runDate
                ? `Generated for ${runDate}. `
                : ""}
              {resetLabel
                ? `Next set available ${resetLabel}.`
                : ""}
            </p>
          )}
        </div>

        {/* ── Health score row ────────────────────── */}
        <div className="px-5 py-3 flex items-center justify-between">
          {resp && (
            <HealthScoreBadge
              score={resp.health_score}
              label={resp.health_label}
            />
          )}
          {totalCount > 0 && (
            <span className="text-xs text-gray-400 dark:text-gray-500">
              {totalCount} recommendation
              {totalCount !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {/* ── Preview rows ───────────────────────── */}
        <div className="border-t border-gray-100 dark:border-gray-800">
          {totalCount === 0 ? (
            <div className="py-8 text-center">
              <svg
                className="w-8 h-8 mx-auto text-gray-300 dark:text-gray-600"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                />
              </svg>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
                No recommendations yet.
              </p>
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                Click Refresh to generate.
              </p>
            </div>
          ) : (
            <>
              {preview.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => setOpen(true)}
                  className={
                    "w-full flex items-center gap-3 " +
                    "px-5 py-3 text-left " +
                    "hover:bg-gray-50 " +
                    "dark:hover:bg-gray-800/50 " +
                    "transition-colors " +
                    "border-b border-gray-50 " +
                    "dark:border-gray-800/50 " +
                    "last:border-b-0"
                  }
                >
                  {/* Severity dot */}
                  <span
                    className={
                      "w-2 h-2 rounded-full shrink-0 " +
                      (SEV_DOT[r.severity] ?? "bg-gray-400")
                    }
                  />
                  {/* Ticker */}
                  <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate min-w-0">
                    {r.ticker || "Portfolio"}
                  </span>
                  {/* Category badge */}
                  <span className="text-[10px] font-medium text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 rounded px-1.5 py-0.5 shrink-0">
                    {categoryLabel(r.category)}
                  </span>
                  {/* Chevron */}
                  <svg
                    className={
                      "w-4 h-4 ml-auto shrink-0 " +
                      (SEV_ICON[r.severity] ??
                        "text-gray-400")
                    }
                    viewBox="0 0 20 20"
                    fill="currentColor"
                  >
                    <path
                      fillRule="evenodd"
                      d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z"
                      clipRule="evenodd"
                    />
                  </svg>
                </button>
              ))}

              {/* View All footer */}
              <button
                type="button"
                onClick={() => setOpen(true)}
                className={
                  "w-full px-5 py-3 text-center " +
                  "text-xs font-semibold " +
                  "text-indigo-600 dark:text-indigo-400 " +
                  "hover:bg-indigo-50 " +
                  "dark:hover:bg-indigo-500/10 " +
                  "transition-colors border-t " +
                  "border-gray-100 dark:border-gray-800"
                }
              >
                View All {totalCount} Recommendation
                {totalCount !== 1 ? "s" : ""} →
              </button>
            </>
          )}
        </div>
      </div>

      {/* Slide-over panel */}
      <RecommendationSlideOver
        open={open}
        onClose={() => setOpen(false)}
        data={resp ?? null}
      />
    </>
  );
}
