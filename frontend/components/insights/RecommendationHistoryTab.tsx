"use client";

import { useState, useMemo } from "react";
import {
  useRecommendationHistory,
  useRecommendationStats,
} from "@/hooks/useInsightsData";
import type {
  HistoryRunItem,
  RecommendationStatsResponse,
} from "@/lib/types";

const PAGE_SIZE = 10;

// ---------------------------------------------------------------
// KPI Card
// ---------------------------------------------------------------

function KpiCard({
  label,
  value,
  tooltip,
}: {
  label: string;
  value: string;
  tooltip?: string;
}) {
  return (
    <div
      className="rounded-2xl border border-gray-200
        dark:border-gray-800 bg-white
        dark:bg-gray-900/80 p-5 flex flex-col gap-1"
      title={tooltip}
    >
      <span
        className="text-xs font-medium text-gray-500
          dark:text-gray-400 uppercase tracking-wide"
      >
        {label}
      </span>
      <span
        className="text-2xl font-semibold
          text-gray-900 dark:text-gray-100"
      >
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------
// Health badge
// ---------------------------------------------------------------

function healthColor(score: number): string {
  if (score >= 80)
    return (
      "bg-emerald-100 text-emerald-700 " +
      "dark:bg-emerald-900/30 dark:text-emerald-400"
    );
  if (score >= 60)
    return (
      "bg-yellow-100 text-yellow-700 " +
      "dark:bg-yellow-900/30 dark:text-yellow-400"
    );
  return (
    "bg-red-100 text-red-700 " +
    "dark:bg-red-900/30 dark:text-red-400"
  );
}

// ---------------------------------------------------------------
// Scope badge
// ---------------------------------------------------------------

function ScopeBadge({ scope }: { scope: string }) {
  if (scope === "india")
    return (
      <span
        className="px-1.5 py-0.5 rounded text-[10px]
          font-semibold bg-orange-100 text-orange-700
          dark:bg-orange-900/30 dark:text-orange-400"
      >
        India
      </span>
    );
  if (scope === "us")
    return (
      <span
        className="px-1.5 py-0.5 rounded text-[10px]
          font-semibold bg-blue-100 text-blue-700
          dark:bg-blue-900/30 dark:text-blue-400"
      >
        US
      </span>
    );
  return (
    <span
      className="px-1.5 py-0.5 rounded text-[10px]
        font-semibold bg-gray-100 text-gray-600
        dark:bg-gray-800 dark:text-gray-400"
    >
      All
    </span>
  );
}

// ---------------------------------------------------------------
// Filter pill
// ---------------------------------------------------------------

function FilterPill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-full px-3 py-1 text-xs " +
        "font-medium transition-colors " +
        (active
          ? "bg-gray-900 text-white " +
            "dark:bg-gray-100 dark:text-gray-900"
          : "bg-gray-100 text-gray-600 " +
            "hover:bg-gray-200 " +
            "dark:bg-gray-800 " +
            "dark:text-gray-400 " +
            "dark:hover:bg-gray-700")
      }
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------
// Collapsible run row
// ---------------------------------------------------------------

function RunRow({ run }: { run: HistoryRunItem }) {
  const [open, setOpen] = useState(false);
  const date = new Date(run.run_date);
  const formatted = date.toLocaleDateString(
    "en-IN",
    {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    },
  );
  const adoptionPct =
    run.total_recommendations > 0
      ? (
          (run.acted_on_count /
            run.total_recommendations) *
          100
        ).toFixed(0)
      : "0";

  return (
    <div
      className="border border-gray-200
        dark:border-gray-700 rounded-xl overflow-hidden"
    >
      <button
        onClick={() => setOpen((p) => !p)}
        className="w-full flex items-center
          justify-between px-4 py-3 text-left
          hover:bg-gray-50 dark:hover:bg-gray-800/50
          transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="text-sm font-medium
              text-gray-900 dark:text-gray-100"
          >
            {formatted}
          </span>
          <ScopeBadge scope={run.scope} />
          <span
            className={
              "px-2 py-0.5 rounded text-xs " +
              "font-medium " +
              healthColor(run.health_score)
            }
          >
            {run.health_label} ({run.health_score})
          </span>
        </div>
        <div
          className="flex items-center gap-4 text-xs
            text-gray-500 dark:text-gray-400 shrink-0"
        >
          <span>
            {run.total_recommendations} recs
          </span>
          <span>
            {run.acted_on_count} acted on
          </span>
          <svg
            className={
              "w-4 h-4 transition-transform " +
              (open ? "rotate-180" : "")
            }
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M19 9l-7 7-7-7"
            />
          </svg>
        </div>
      </button>

      {open && (
        <div
          className="px-4 py-3 border-t
            border-gray-200 dark:border-gray-700
            bg-gray-50 dark:bg-gray-800/30"
        >
          <div
            className="grid grid-cols-2 sm:grid-cols-4
              gap-3 text-sm"
          >
            <div>
              <span className="text-gray-500 dark:text-gray-400">
                Run ID
              </span>
              <p
                className="font-mono text-xs
                  text-gray-700 dark:text-gray-300
                  truncate"
              >
                {run.run_id}
              </p>
            </div>
            <div>
              <span className="text-gray-500 dark:text-gray-400">
                Health Score
              </span>
              <p
                className="font-semibold text-gray-900
                  dark:text-gray-100"
              >
                {run.health_score}/100
              </p>
            </div>
            <div>
              <span className="text-gray-500 dark:text-gray-400">
                Total Recommendations
              </span>
              <p
                className="font-semibold text-gray-900
                  dark:text-gray-100"
              >
                {run.total_recommendations}
              </p>
            </div>
            <div>
              <span className="text-gray-500 dark:text-gray-400">
                Adoption
              </span>
              <p
                className="font-semibold text-gray-900
                  dark:text-gray-100"
              >
                {adoptionPct}% ({run.acted_on_count}/
                {run.total_recommendations})
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Stats KPI row
// ---------------------------------------------------------------

function StatsRow({
  stats,
}: {
  stats: RecommendationStatsResponse;
}) {
  const fmtPct = (
    v: number | null | undefined,
  ): string =>
    v == null ? "\u2014" : `${v.toFixed(1)}%`;

  const fmtReturn = (
    v: number | null | undefined,
  ): string =>
    v == null
      ? "\u2014"
      : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
      <KpiCard
        label="Hit Rate 30d"
        value={fmtPct(stats.hit_rate_30d)}
        tooltip="Percentage correct at 30 days"
      />
      <KpiCard
        label="Hit Rate 60d"
        value={fmtPct(stats.hit_rate_60d)}
        tooltip="Percentage correct at 60 days"
      />
      <KpiCard
        label="Avg Excess Return"
        value={fmtReturn(stats.avg_return_30d)}
        tooltip="Average return vs Nifty 50 at 30d"
      />
      <KpiCard
        label="Adoption Rate"
        value={fmtPct(stats.adoption_rate_pct)}
        tooltip="Percentage acted on by user"
      />
    </div>
  );
}

// ---------------------------------------------------------------
// Main component
// ---------------------------------------------------------------

type ScopeFilter = "all" | "india" | "us";

const TIME_RANGES = [
  { value: 7, label: "7D" },
  { value: 30, label: "1M" },
  { value: 90, label: "3M" },
  { value: 180, label: "6M" },
  { value: 270, label: "9M" },
  { value: 365, label: "1Y" },
] as const;

export function RecommendationHistoryTab() {
  const history = useRecommendationHistory(12);
  const stats = useRecommendationStats();
  const [scopeFilter, setScopeFilter] =
    useState<ScopeFilter>("india");
  const [daysBack, setDaysBack] = useState(90);
  const [page, setPage] = useState(0);

  // Filter runs by scope + time range
  const filtered = useMemo(() => {
    const runs = history.value?.runs ?? [];
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - daysBack);

    return runs.filter((r) => {
      if (
        scopeFilter !== "all" &&
        r.scope !== scopeFilter
      )
        return false;
      const rd = new Date(r.run_date);
      return rd >= cutoff;
    });
  }, [history.value, scopeFilter, daysBack]);

  // Paginate
  const totalPages = Math.max(
    1,
    Math.ceil(filtered.length / PAGE_SIZE),
  );
  const pageRuns = filtered.slice(
    page * PAGE_SIZE,
    (page + 1) * PAGE_SIZE,
  );

  // Reset page on filter change
  const handleScopeChange = (s: ScopeFilter) => {
    setScopeFilter(s);
    setPage(0);
  };
  const handleTimeChange = (days: number) => {
    setDaysBack(days);
    setPage(0);
  };

  if (history.loading || stats.loading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div
          className="grid grid-cols-2
            sm:grid-cols-4 gap-4"
        >
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-24 rounded-2xl bg-gray-200
                dark:bg-gray-700"
            />
          ))}
        </div>
        <div
          className="h-48 rounded-xl bg-gray-200
            dark:bg-gray-700"
        />
      </div>
    );
  }

  if (history.error || stats.error) {
    return (
      <div
        className="rounded-2xl border border-red-200
          dark:border-red-800 bg-white
          dark:bg-gray-900/80 p-5 text-red-600
          dark:text-red-400"
      >
        Failed to load recommendation history.
      </div>
    );
  }

  const allRuns = history.value?.runs ?? [];
  const statsData = stats.value;

  if (allRuns.length === 0 && !statsData) {
    return (
      <div
        className="rounded-2xl border border-gray-200
          dark:border-gray-800 bg-white
          dark:bg-gray-900/80 p-8 text-center
          text-gray-500 dark:text-gray-400"
      >
        No recommendation history. Generate
        recommendations from the dashboard first.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* KPI cards */}
      {statsData && <StatsRow stats={statsData} />}

      {/* Filters */}
      <div className="space-y-3">
        <div
          className="flex items-center
            justify-between flex-wrap gap-3"
        >
          <h3
            className="text-sm font-semibold
              text-gray-700 dark:text-gray-300
              uppercase tracking-wide"
          >
            Run History
          </h3>
          <div className="flex items-center gap-4 flex-wrap">
            {/* Scope filter */}
            <div className="flex gap-1.5">
              {(
                [
                  { value: "all", label: "All" },
                  {
                    value: "india",
                    label: "India",
                  },
                  { value: "us", label: "US" },
                ] as const
              ).map((o) => (
                <FilterPill
                  key={o.value}
                  label={o.label}
                  active={scopeFilter === o.value}
                  onClick={() =>
                    handleScopeChange(o.value)
                  }
                />
              ))}
            </div>
            {/* Time range filter */}
            <div
              className="flex gap-1 border-l
                border-gray-200 dark:border-gray-700
                pl-3"
            >
              {TIME_RANGES.map((t) => (
                <FilterPill
                  key={t.value}
                  label={t.label}
                  active={daysBack === t.value}
                  onClick={() =>
                    handleTimeChange(t.value)
                  }
                />
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Run list */}
      {filtered.length === 0 ? (
        <p
          className="text-sm text-gray-500
            dark:text-gray-400"
        >
          No runs found for the selected filter.
        </p>
      ) : (
        <div className="space-y-2">
          {pageRuns.map((run) => (
            <RunRow key={run.run_id} run={run} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div
          className="flex items-center
            justify-between pt-2"
        >
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
            className="text-xs font-medium px-3
              py-1.5 rounded-lg border
              border-gray-200 dark:border-gray-700
              disabled:opacity-40 hover:bg-gray-50
              dark:hover:bg-gray-800
              transition-colors"
          >
            &larr; Previous
          </button>
          <span
            className="text-xs text-gray-500
              dark:text-gray-400"
          >
            Page {page + 1} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
            className="text-xs font-medium px-3
              py-1.5 rounded-lg border
              border-gray-200 dark:border-gray-700
              disabled:opacity-40 hover:bg-gray-50
              dark:hover:bg-gray-800
              transition-colors"
          >
            Next &rarr;
          </button>
        </div>
      )}
    </div>
  );
}
