"use client";
/**
 * W5: LLM Portfolio Recommendations (ASETPLTFRM-298).
 *
 * Displays health score, filter pills (tier + severity),
 * and a list of RecommendationCard components.
 * Refresh button triggers a manual recommendation run.
 */

import { useState, useCallback, useMemo } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";
import { HealthScoreBadge } from "./HealthScoreBadge";
import { RecommendationCard } from "./RecommendationCard";
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
}

/* ── Filter types ──────────────────────────────────── */

type TierFilter =
  | "all"
  | "portfolio"
  | "watchlist"
  | "discovery";
type SeverityFilter =
  | "all"
  | "high"
  | "medium"
  | "low";

const TIER_OPTIONS: { value: TierFilter; label: string }[] =
  [
    { value: "all", label: "All" },
    { value: "portfolio", label: "Portfolio" },
    { value: "watchlist", label: "Watchlist" },
    { value: "discovery", label: "Discovery" },
  ];

const SEVERITY_OPTIONS: {
  value: SeverityFilter;
  label: string;
}[] = [
  { value: "all", label: "All" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

/* ── Pill button helper ────────────────────────────── */

function FilterPill<T extends string>({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value: T;
  active: boolean;
  onClick: (v: T) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      className={
        "rounded-full px-3 py-1 text-xs font-medium " +
        "transition-colors " +
        (active
          ? "bg-gray-900 text-white dark:bg-gray-100 " +
            "dark:text-gray-900"
          : "bg-gray-100 text-gray-600 hover:bg-gray-200 " +
            "dark:bg-gray-800 dark:text-gray-400 " +
            "dark:hover:bg-gray-700")
      }
    >
      {label}
    </button>
  );
}

/* ── Date formatting ───────────────────────────────── */

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/* ── Widget ────────────────────────────────────────── */

export function RecommendationsWidget({ data }: Props) {
  const [tier, setTier] = useState<TierFilter>("all");
  const [severity, setSeverity] =
    useState<SeverityFilter>("all");
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await apiFetch(
        `${API_URL}/dashboard/portfolio/recommendations/refresh`,
        { method: "POST" },
      );
      data.mutate?.();
    } catch {
      // swallow — mutate will pick up stale data
    } finally {
      setRefreshing(false);
    }
  }, [data]);

  // Apply filters
  const filtered = useMemo(() => {
    const recs = data.value?.recommendations ?? [];
    return recs.filter((r) => {
      if (tier !== "all" && r.tier !== tier) return false;
      if (severity !== "all" && r.severity !== severity)
        return false;
      return true;
    });
  }, [data.value, tier, severity]);

  /* ── Loading ───────────────────────────────────── */
  if (data.loading) return <WidgetSkeleton className="h-72" />;
  if (data.error) return <WidgetError message={data.error} />;

  const resp = data.value;
  const hasRecs = (resp?.recommendations ?? []).length > 0;

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm flex flex-col">
      {/* ── Header ───────────────────────────────── */}
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Recommendations
          </h3>
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing}
            className={
              "inline-flex items-center gap-1.5 " +
              "rounded-lg px-3 py-1.5 text-xs font-medium " +
              "border border-gray-200 dark:border-gray-700 " +
              "bg-white dark:bg-gray-800 " +
              "text-gray-700 dark:text-gray-300 " +
              "hover:bg-gray-50 dark:hover:bg-gray-700 " +
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
            {refreshing ? "Generating..." : "Refresh"}
          </button>
        </div>

        {/* Health score + date row */}
        {resp && (
          <div className="mt-3 flex items-center justify-between">
            <HealthScoreBadge
              score={resp.health_score}
              label={resp.health_label}
            />
            {resp.generated_at && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500">
                {formatDate(resp.generated_at)}
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Filter pills ─────────────────────────── */}
      {hasRecs && (
        <div className="px-5 py-3 border-b border-gray-100 dark:border-gray-800 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {TIER_OPTIONS.map((o) => (
              <FilterPill
                key={o.value}
                label={o.label}
                value={o.value}
                active={tier === o.value}
                onClick={setTier}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {SEVERITY_OPTIONS.map((o) => (
              <FilterPill
                key={o.value}
                label={o.label}
                value={o.value}
                active={severity === o.value}
                onClick={setSeverity}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Recommendation cards ─────────────────── */}
      <div className="flex-1 overflow-y-auto max-h-[480px] p-4 space-y-3">
        {!hasRecs ? (
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
        ) : filtered.length === 0 ? (
          <div className="py-6 text-center">
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No results match the current filters.
            </p>
          </div>
        ) : (
          filtered.map((r) => (
            <RecommendationCard key={r.id} rec={r} />
          ))
        )}
      </div>
    </div>
  );
}
