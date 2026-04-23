"use client";

import { useEffect, useMemo, useState } from "react";
import {
  useRecommendationHistory,
  useRecommendationStats,
  useRunDetail,
} from "@/hooks/useInsightsData";
import {
  ScopeBadge,
  RunTypeBadge,
  TierBadge,
  CategoryPill,
  SeverityPill,
  healthBadgeClass,
} from "@/components/recommendations/badges";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { downloadCsv } from "@/lib/downloadCsv";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import { RecActionButton } from "@/components/recommendations/RecActionButton";
import type {
  HistoryRunItem,
  RecommendationItem,
  RecommendationResponse,
  RecommendationStatsResponse,
} from "@/lib/types";

const DEFAULT_PAGE_SIZE = 10;
const PAGE_SIZE_OPTIONS = [5, 10, 25, 50] as const;

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
// Per-rec eye-icon + rationale modal (read-only)
// ---------------------------------------------------------------

function EyeIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path d="M10 12.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z" />
      <path
        fillRule="evenodd"
        d="M.664 10.59a1.651 1.651 0 010-1.186A10.004 10.004 0 0110 3c4.257 0 7.893 2.66 9.336 6.41.147.382.147.804 0 1.186A10.004 10.004 0 0110 17c-4.257 0-7.893-2.66-9.336-6.41zM14 10a4 4 0 11-8 0 4 4 0 018 0z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function RecRow({
  rec,
  onView,
}: {
  rec: RecommendationItem;
  onView: (r: RecommendationItem) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/60">
      <span className="min-w-[90px] font-mono text-xs font-semibold text-gray-900 dark:text-gray-100">
        {rec.ticker ?? "—"}
      </span>
      <TierBadge tier={rec.tier} />
      <CategoryPill category={rec.category} />
      <SeverityPill severity={rec.severity} />
      <span className="text-[11px] font-medium uppercase text-gray-600 dark:text-gray-400">
        {rec.action}
      </span>
      <span
        className="ml-1 max-w-md flex-1 truncate text-[11px] text-gray-500 dark:text-gray-400"
        title={rec.rationale}
      >
        {rec.rationale}
      </span>
      <div className="ml-auto flex items-center gap-1">
        {rec.ticker && (
          <RecActionButton
            ticker={rec.ticker}
            action={rec.action}
            actedOn={!!rec.acted_on_date}
          />
        )}
        <button
          type="button"
          onClick={() => onView(rec)}
          className="rounded-md bg-gray-100 p-1.5 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
          aria-label={`View rationale ${rec.id}`}
          title="View rationale"
        >
          <EyeIcon />
        </button>
      </div>
    </div>
  );
}

function RationaleModal({
  rec,
  onClose,
}: {
  rec: RecommendationItem | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!rec) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () =>
      document.removeEventListener("keydown", handler);
  }, [rec, onClose]);

  if (!rec) return null;

  const signals = Object.entries(rec.data_signals ?? {});

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl mx-4 max-h-[80vh] overflow-auto rounded-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              {rec.ticker ?? "Recommendation"}
              <span className="ml-2 text-sm font-normal text-gray-500 dark:text-gray-400">
                {rec.action.toUpperCase()}
              </span>
            </h3>
            <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
              {rec.tier} · {rec.category} · {rec.severity}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-200"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              className="h-5 w-5"
            >
              <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
            </svg>
          </button>
        </div>

        <div className="mt-4 space-y-3">
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Rationale
            </h4>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-gray-700 dark:text-gray-300">
              {rec.rationale}
            </p>
          </section>

          {rec.expected_impact && (
            <section>
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Expected Impact
              </h4>
              <p className="mt-1 text-sm italic text-gray-600 dark:text-gray-400">
                {rec.expected_impact}
              </p>
            </section>
          )}

          {signals.length > 0 && (
            <section>
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Data Signals
              </h4>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {signals.map(([k, v]) => (
                  <span
                    key={k}
                    className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-200"
                  >
                    {k.replace(/_/g, " ")}:{" "}
                    <span className="font-semibold">
                      {typeof v === "number"
                        ? v.toFixed(2)
                        : String(v)}
                    </span>
                  </span>
                ))}
              </div>
            </section>
          )}

          {(rec.price_at_rec != null ||
            rec.target_price != null ||
            rec.expected_return_pct != null) && (
            <section>
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Pricing
              </h4>
              <dl className="mt-1 grid grid-cols-3 gap-3 text-xs">
                {rec.price_at_rec != null && (
                  <div>
                    <dt className="text-gray-500 dark:text-gray-400">
                      Price at rec
                    </dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {rec.price_at_rec.toLocaleString()}
                    </dd>
                  </div>
                )}
                {rec.target_price != null && (
                  <div>
                    <dt className="text-gray-500 dark:text-gray-400">
                      Target
                    </dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {rec.target_price.toLocaleString()}
                    </dd>
                  </div>
                )}
                {rec.expected_return_pct != null && (
                  <div>
                    <dt className="text-gray-500 dark:text-gray-400">
                      Expected return
                    </dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {rec.expected_return_pct.toFixed(2)}%
                    </dd>
                  </div>
                )}
              </dl>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Collapsible run row
// ---------------------------------------------------------------

function RunRow({
  run,
  onViewRec,
}: {
  run: HistoryRunItem;
  onViewRec: (r: RecommendationItem) => void;
}) {
  const [open, setOpen] = useState(false);
  const detail = useRunDetail(open ? run.run_id : null);
  const date = new Date(run.created_at ?? run.run_date);
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
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen((p) => !p)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ")
            setOpen((p) => !p);
        }}
        className="w-full flex items-center
          justify-between px-4 py-3 text-left
          cursor-pointer select-none
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
          <RunTypeBadge runType={run.run_type} />
          <span
            className={
              "px-2 py-0.5 rounded text-xs " +
              "font-medium " +
              healthBadgeClass(run.health_score)
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
      </div>

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

          {/* Inline stock recommendations list */}
          <div className="mt-4 space-y-1.5">
            {detail.loading && (
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Loading recommendations…
              </p>
            )}
            {detail.error && (
              <p className="text-xs text-red-600 dark:text-red-400">
                Failed to load recommendations.
              </p>
            )}
            {!detail.loading &&
              !detail.error &&
              detail.value?.recommendations?.length ===
                0 && (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  No recommendations in this run.
                </p>
              )}
            {detail.value?.recommendations?.map((rec) => (
              <RecRow
                key={rec.id}
                rec={rec}
                onView={onViewRec}
              />
            ))}
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
  ): string => `${(v ?? 0).toFixed(1)}%`;

  const fmtReturn = (
    v: number | null | undefined,
  ): string => {
    const n = v ?? 0;
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  };

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
  const [scopeFilter, setScopeFilter] =
    useState<ScopeFilter>("india");
  const stats = useRecommendationStats(scopeFilter);
  const [daysBack, setDaysBack] = useState(90);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<number>(
    DEFAULT_PAGE_SIZE,
  );
  const [viewRec, setViewRec] =
    useState<RecommendationItem | null>(null);
  const [downloading, setDownloading] = useState(false);

  // Filter runs by scope + time range, then sort
  // by created_at desc (falling back to run_date) so
  // the freshest run is always on top.
  const filtered = useMemo(() => {
    const runs = history.value?.runs ?? [];
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - daysBack);

    return runs
      .filter((r) => {
        if (
          scopeFilter !== "all" &&
          r.scope !== scopeFilter
        )
          return false;
        const rd = new Date(r.run_date);
        return rd >= cutoff;
      })
      .slice()
      .sort((a, b) => {
        const ta = new Date(
          a.created_at ?? a.run_date,
        ).getTime();
        const tb = new Date(
          b.created_at ?? b.run_date,
        ).getTime();
        return tb - ta;
      });
  }, [history.value, scopeFilter, daysBack]);

  // Paginate
  const totalPages = Math.max(
    1,
    Math.ceil(filtered.length / pageSize),
  );
  const safePage = Math.min(page, totalPages - 1);
  const pageRuns = filtered.slice(
    safePage * pageSize,
    (safePage + 1) * pageSize,
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

  // Fetch every filtered run's detail in parallel,
  // flatten child recs with run metadata, export CSV.
  const handleDownload = async () => {
    if (filtered.length === 0 || downloading) return;
    setDownloading(true);
    try {
      const details = await Promise.all(
        filtered.map(async (run) => {
          const r = await apiFetch(
            `${API_URL}/dashboard/portfolio/recommendations/${run.run_id}`,
          );
          if (!r.ok) return null;
          return (await r.json()) as RecommendationResponse;
        }),
      );
      const rows = details.flatMap((detail, i) => {
        if (!detail) return [];
        const run = filtered[i];
        return detail.recommendations.map((rec) => ({
          run_date: run.run_date,
          run_type: run.run_type,
          scope: run.scope,
          health_score: run.health_score,
          health_label: run.health_label,
          run_id: run.run_id,
          ticker: rec.ticker ?? "",
          tier: rec.tier,
          category: rec.category,
          severity: rec.severity,
          action: rec.action,
          rationale: rec.rationale,
          expected_impact:
            rec.expected_impact ?? "",
          price_at_rec: rec.price_at_rec ?? "",
          target_price: rec.target_price ?? "",
          expected_return_pct:
            rec.expected_return_pct ?? "",
          acted_on_date: rec.acted_on_date ?? "",
          status: rec.status,
        }));
      });
      if (rows.length > 0) {
        downloadCsv(
          rows,
          [
            { key: "run_date", header: "Run Date" },
            { key: "run_type", header: "Run Type" },
            { key: "scope", header: "Scope" },
            { key: "health_score", header: "Health Score" },
            { key: "health_label", header: "Health" },
            { key: "run_id", header: "Run ID" },
            { key: "ticker", header: "Ticker" },
            { key: "tier", header: "Tier" },
            { key: "category", header: "Category" },
            { key: "severity", header: "Severity" },
            { key: "action", header: "Action" },
            { key: "rationale", header: "Rationale" },
            {
              key: "expected_impact",
              header: "Expected Impact",
            },
            {
              key: "price_at_rec",
              header: "Price at Rec",
            },
            {
              key: "target_price",
              header: "Target Price",
            },
            {
              key: "expected_return_pct",
              header: "Expected Return %",
            },
            {
              key: "acted_on_date",
              header: "Acted On",
            },
            { key: "status", header: "Status" },
          ],
          "my-recommendations",
        );
      }
    } finally {
      setDownloading(false);
    }
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
            <RunRow
              key={run.run_id}
              run={run}
              onViewRec={setViewRec}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {filtered.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 text-xs text-gray-600 dark:text-gray-400">
          <div className="flex items-center gap-2">
            <span>{filtered.length} runs</span>
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(0);
              }}
              className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}/page
                </option>
              ))}
            </select>
            <DownloadCsvButton
              onClick={handleDownload}
              loading={downloading}
              disabled={filtered.length === 0}
              aria-label="Download CSV"
              title={
                downloading
                  ? "Preparing CSV…"
                  : "Download CSV"
              }
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={safePage <= 0}
              onClick={() =>
                setPage((p) => Math.max(0, p - 1))
              }
              className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              Prev
            </button>
            <span className="tabular-nums">
              {safePage + 1} / {totalPages}
            </span>
            <button
              type="button"
              disabled={safePage >= totalPages - 1}
              onClick={() =>
                setPage((p) =>
                  Math.min(totalPages - 1, p + 1),
                )
              }
              className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Per-rec rationale modal */}
      <RationaleModal
        rec={viewRec}
        onClose={() => setViewRec(null)}
      />
    </div>
  );
}
