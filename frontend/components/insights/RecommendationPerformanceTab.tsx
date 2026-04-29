"use client";
/**
 * Cohort-bucketed performance view for the
 * recommendations panel — sibling sub-tab to the
 * existing History list.
 *
 * A *cohort* bucket groups recommendations by when
 * they were issued (week / month / quarter, IST).
 * Granularity choice drives both bucket size AND the
 * primary outcome horizon emphasised in the chart:
 *
 *   Weekly    → buckets weekly, metrics at 7-day  outcome
 *   Monthly   → buckets monthly, metrics at 30-day outcome
 *   Quarterly → buckets quarterly, metrics at 90-day outcome
 *
 * That mapping keeps "weekly performance" actually
 * weekly. The recommendation_outcomes job persists
 * outcomes at all four horizons {7, 30, 60, 90} so a
 * user switching from Weekly to Monthly sees the
 * appropriate horizon's data instantly.
 *
 * Latest run still surfaces via the dashboard widget;
 * this tab is for "how have past cohorts performed".
 */

import { useMemo, useState } from "react";
import { useRecommendationPerformance } from
  "@/hooks/useInsightsData";
import { SimpleBarChart, type BarSeries } from
  "@/components/charts/SimpleBarChart";
import { DownloadCsvButton } from
  "@/components/common/DownloadCsvButton";
import { InfoTooltip } from
  "@/components/common/InfoTooltip";
import type { PerfBucket, PerfSummary } from "@/lib/types";

type Granularity = "week" | "month" | "quarter";
type Scope = "all" | "india" | "us";
type Horizon = 7 | 30 | 60 | 90;

const GRANULARITIES: {
  value: Granularity;
  label: string;
}[] = [
  { value: "week", label: "Weekly" },
  { value: "month", label: "Monthly" },
  { value: "quarter", label: "Quarterly" },
];

const SCOPES: { value: Scope; label: string }[] = [
  { value: "all", label: "All" },
  { value: "india", label: "India" },
  { value: "us", label: "US" },
];

// Granularity → primary horizon. Weekly emphasises the
// 7-day outcome, monthly the 30-day, quarterly the 90-day.
const HORIZON_FOR: Record<Granularity, Horizon> = {
  week: 7,
  month: 30,
  quarter: 90,
};

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)}%`;
}

function fmtReturn(
  v: number | null | undefined,
): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function returnColor(
  v: number | null | undefined,
): string {
  if (v == null) return "text-gray-500";
  if (v > 0) return "text-emerald-600 dark:text-emerald-400";
  if (v < 0) return "text-rose-600 dark:text-rose-400";
  return "text-gray-500";
}

// Type-narrowed accessors so TS verifies we're reading
// the fields that exist on PerfBucket / PerfSummary.
function bucketHitRate(
  b: PerfBucket, h: Horizon,
): number | null | undefined {
  if (h === 7) return b.hit_rate_7d;
  if (h === 30) return b.hit_rate_30d;
  if (h === 60) return b.hit_rate_60d;
  return b.hit_rate_90d;
}

function bucketReturn(
  b: PerfBucket, h: Horizon,
): number | null | undefined {
  if (h === 7) return b.avg_return_7d;
  if (h === 30) return b.avg_return_30d;
  if (h === 60) return b.avg_return_60d;
  return b.avg_return_90d;
}

function bucketExcess(
  b: PerfBucket, h: Horizon,
): number | null | undefined {
  if (h === 7) return b.avg_excess_7d;
  if (h === 30) return b.avg_excess_30d;
  if (h === 60) return b.avg_excess_60d;
  return b.avg_excess_90d;
}

function summaryHitRate(
  s: PerfSummary | undefined, h: Horizon,
): number | null | undefined {
  if (!s) return undefined;
  if (h === 7) return s.hit_rate_7d;
  if (h === 30) return s.hit_rate_30d;
  if (h === 60) return s.hit_rate_60d;
  return s.hit_rate_90d;
}

function summaryExcess(
  s: PerfSummary | undefined, h: Horizon,
): number | null | undefined {
  if (!s) return undefined;
  if (h === 7) return s.avg_excess_7d;
  if (h === 30) return s.avg_excess_30d;
  if (h === 60) return s.avg_excess_60d;
  return s.avg_excess_90d;
}

interface KpiTileProps {
  label: string;
  value: string;
  tooltip?: string;
  /** Rich popover content. Falls back to ``tooltip``. */
  info?: React.ReactNode;
  valueClass?: string;
}

function KpiTile({
  label, value, tooltip, info, valueClass,
}: KpiTileProps) {
  return (
    <div
      className={
        "rounded-md border border-gray-200 " +
        "dark:border-gray-700 bg-white " +
        "dark:bg-gray-800 px-3 py-2"
      }
      title={info ? undefined : tooltip}
    >
      <div
        className={
          "inline-flex items-center text-[10px] " +
          "uppercase tracking-wide " +
          "text-gray-500 dark:text-gray-400"
        }
      >
        {label}
        {info && <InfoTooltip>{info}</InfoTooltip>}
      </div>
      <div
        className={
          "mt-0.5 text-base font-semibold " +
          (valueClass
            ?? "text-gray-900 dark:text-gray-100")
        }
      >
        {value}
      </div>
    </div>
  );
}

interface PillStripProps<T extends string> {
  options: { value: T; label: string }[];
  selected: T;
  onChange: (v: T) => void;
}

function PillStrip<T extends string>({
  options, selected, onChange,
}: PillStripProps<T>) {
  return (
    <div className="inline-flex rounded-md border border-gray-300 dark:border-gray-600 overflow-hidden">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={
            "px-2.5 py-1 text-xs font-medium " +
            "transition-colors " +
            (selected === o.value
              ? "bg-indigo-600 text-white"
              : "bg-white dark:bg-gray-800 " +
                "text-gray-700 dark:text-gray-200 " +
                "hover:bg-gray-100 " +
                "dark:hover:bg-gray-700")
          }
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function buildCsv(
  buckets: PerfBucket[], horizon: Horizon,
): string {
  const cols: (keyof PerfBucket)[] = [
    "bucket_label",
    "bucket_start",
    "total_recs",
    "acted_on_count",
    "pending_count",
    `hit_rate_${horizon}d` as keyof PerfBucket,
    `avg_return_${horizon}d` as keyof PerfBucket,
    `avg_excess_${horizon}d` as keyof PerfBucket,
  ];
  const header = cols.join(",");
  const lines = buckets.map((b) =>
    cols
      .map((c) => {
        const v = b[c];
        if (v == null) return "";
        return String(v);
      })
      .join(","),
  );
  return [header, ...lines].join("\n");
}

export function RecommendationPerformanceTab() {
  const [granularity, setGranularity] =
    useState<Granularity>("month");
  const [scope, setScope] = useState<Scope>("all");
  const [actedOnOnly, setActedOnOnly] =
    useState<boolean>(false);

  const horizon = HORIZON_FOR[granularity];

  const perf = useRecommendationPerformance({
    granularity,
    scope,
    actedOnOnly,
    monthsBack: 14,
  });

  const buckets = useMemo(
    () => perf.value?.buckets ?? [],
    [perf.value?.buckets],
  );
  const summary = perf.value?.summary;
  const totalPending =
    summary?.pending_count ?? 0;

  // Bar chart 1: hit rate per bucket at the primary
  // horizon for the chosen granularity. One series.
  const hitRateChart = useMemo(() => {
    const categories = buckets.map((b) => b.bucket_label);
    const values = buckets.map((b) => {
      const v = bucketHitRate(b, horizon);
      return v ?? 0;
    });
    const series: BarSeries[] = [
      { name: `Hit rate ${horizon}d`, values },
    ];
    return { categories, series };
  }, [buckets, horizon]);

  // Bar chart 2: avg return vs benchmark at the
  // primary horizon. Paired bars per bucket.
  const returnChart = useMemo(() => {
    const categories = buckets.map((b) => b.bucket_label);
    const recReturn = buckets.map(
      (b) => bucketReturn(b, horizon) ?? 0,
    );
    const benchReturn = buckets.map((b) => {
      const r = bucketReturn(b, horizon);
      const e = bucketExcess(b, horizon);
      if (r == null || e == null) return 0;
      return r - e;
    });
    const series: BarSeries[] = [
      { name: "Recommendation", values: recReturn },
      { name: "Benchmark", values: benchReturn },
    ];
    return { categories, series };
  }, [buckets, horizon]);

  // Bar chart 3 (NEW): activity per bucket — recs
  // issued. Always renderable (doesn't depend on
  // outcomes), so the tab shows *something* even
  // when the cohort is too young for outcomes.
  const activityChart = useMemo(() => {
    const categories = buckets.map((b) => b.bucket_label);
    const issued = buckets.map((b) => b.total_recs);
    const acted = buckets.map((b) => b.acted_on_count);
    const series: BarSeries[] = [
      { name: "Issued", values: issued },
      { name: "Acted on", values: acted },
    ];
    return { categories, series };
  }, [buckets]);

  const handleDownload = () => {
    const csv = buildCsv(buckets, horizon);
    const blob = new Blob([csv], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (
      `recommendation-performance-${granularity}` +
      `-${scope}.csv`
    );
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      {/* ── Filter row ──────────────────────────── */}
      <div
        className={
          "flex flex-wrap items-center gap-2 " +
          "justify-between"
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span
              className={
                "text-[11px] uppercase tracking-wide " +
                "text-gray-500 dark:text-gray-400"
              }
            >
              Period
            </span>
            <PillStrip
              options={GRANULARITIES}
              selected={granularity}
              onChange={setGranularity}
            />
          </div>
          <div className="flex items-center gap-2">
            <span
              className={
                "text-[11px] uppercase tracking-wide " +
                "text-gray-500 dark:text-gray-400"
              }
            >
              Scope
            </span>
            <PillStrip
              options={SCOPES}
              selected={scope}
              onChange={setScope}
            />
          </div>
          <label
            className={
              "inline-flex items-center gap-1.5 " +
              "text-xs text-gray-700 " +
              "dark:text-gray-300 cursor-pointer"
            }
          >
            <input
              type="checkbox"
              checked={actedOnOnly}
              onChange={(e) =>
                setActedOnOnly(e.target.checked)
              }
              className="h-3.5 w-3.5"
              data-testid="acted-on-toggle"
            />
            Acted-on only
          </label>
        </div>
        <DownloadCsvButton
          onClick={handleDownload}
          disabled={buckets.length === 0}
          data-testid="perf-csv"
        />
      </div>

      {/* ── KPI tiles ───────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiTile
          label="Total recs"
          value={String(summary?.total_recs ?? 0)}
          info={
            <>
              <p className="font-semibold mb-1">
                What
              </p>
              <p className="mb-2">
                Recommendations issued in the selected
                window (last 14 months by default).
              </p>
              <p>
                Filters above this row (Scope,
                Acted-on only) narrow the count
                immediately.
              </p>
            </>
          }
        />
        <KpiTile
          label="Acted on"
          value={String(summary?.acted_on_count ?? 0)}
          info={
            <>
              <p className="font-semibold mb-1">
                What
              </p>
              <p className="mb-2">
                Of those recs, how many the user
                actually acted on — added, replaced,
                or trimmed the suggested ticker in
                their portfolio.
              </p>
              <p>
                <span className="font-semibold">
                  How:
                </span>{" "}
                rec&apos;s{" "}
                <span className="font-mono text-[11px]">
                  acted_on_date
                </span>{" "}
                stamp is set automatically when a
                portfolio change matches a ticker.
              </p>
            </>
          }
        />
        <KpiTile
          label={`Hit rate ${horizon}d`}
          value={fmtPct(
            summaryHitRate(summary, horizon),
          )}
          info={
            <>
              <p className="font-semibold mb-1">
                What
              </p>
              <p className="mb-2">
                Share of recs whose return beat the
                benchmark {horizon} days after issue.
                Granularity = {granularity}, so this
                tile tracks the {horizon}-day horizon.
              </p>
              <p className="font-semibold mb-1">How</p>
              <p>
                A &ldquo;hit&rdquo; = excess return
                (rec − benchmark) &gt; 0 at the{" "}
                {horizon}-day outcome check.{" "}
                <span className="font-semibold">
                  Formula:
                </span>{" "}
                hits ÷ recs with a {horizon}d outcome
                × 100.
              </p>
            </>
          }
        />
        <KpiTile
          label={`Avg excess ${horizon}d`}
          value={fmtReturn(
            summaryExcess(summary, horizon),
          )}
          valueClass={returnColor(
            summaryExcess(summary, horizon),
          )}
          info={
            <>
              <p className="font-semibold mb-1">
                What
              </p>
              <p className="mb-2">
                Mean gap between each rec&apos;s return
                and its benchmark return at the{" "}
                {horizon}-day check. Positive = recs
                beat the benchmark on average.
              </p>
              <p className="font-semibold mb-1">How</p>
              <p className="mb-2">
                Per outcome:{" "}
                <span className="font-mono text-[11px]">
                  excess = rec_return − benchmark_return
                </span>
                . The tile shows the mean across all{" "}
                {horizon}-day outcomes in scope.
              </p>
              <p className="text-amber-700 dark:text-amber-300">
                <span className="font-semibold">
                  Heads up:
                </span>{" "}
                benchmark_return_pct is currently 0
                in the daily outcomes job (TODO to
                wire to a real index), so excess ≡
                recommendation return until that&apos;s
                fixed.
              </p>
            </>
          }
        />
      </div>

      {/* ── Stale chip ──────────────────────────── */}
      {totalPending > 0 && (
        <div
          className={
            "inline-flex items-center gap-1.5 " +
            "rounded-full bg-amber-50 " +
            "dark:bg-amber-900/30 border " +
            "border-amber-300 dark:border-amber-700 " +
            "px-2.5 py-1 text-xs " +
            "text-amber-800 dark:text-amber-200"
          }
          title={
            "Recommendations younger than the "
            + "selected horizon don't have outcomes "
            + "yet — they will appear in the metrics "
            + "once the daily recommendation_outcomes "
            + "job processes them."
          }
          data-testid="perf-stale-chip"
        >
          ⚠ {totalPending} recommendation
          {totalPending === 1 ? "" : "s"}{" "}
          under {horizon} days, outcomes pending
        </div>
      )}

      {/* ── Loading / empty states ───────────────── */}
      {perf.loading && (
        <div
          className={
            "flex items-center justify-center h-32 " +
            "text-sm text-gray-500 " +
            "dark:text-gray-400"
          }
        >
          Loading performance data…
        </div>
      )}
      {perf.error && (
        <div className="text-sm text-rose-600 dark:text-rose-400">
          Failed to load performance: {perf.error}
        </div>
      )}
      {!perf.loading
        && !perf.error
        && buckets.length === 0 && (
        <div
          className={
            "rounded border border-dashed " +
            "border-gray-300 dark:border-gray-600 " +
            "p-8 text-center text-sm " +
            "text-gray-500 dark:text-gray-400"
          }
        >
          <p className="font-medium">
            No recommendations in this window.
          </p>
          <p className="mt-1 text-xs">
            Try widening the period or switching scope.
          </p>
        </div>
      )}

      {/* ── Charts ──────────────────────────────── */}
      {!perf.loading
        && !perf.error
        && buckets.length > 0 && (
        <div className="space-y-4">
          {/* Activity is always renderable —
              regardless of outcome availability. */}
          <div
            className={
              "rounded-md border border-gray-200 " +
              "dark:border-gray-700 bg-white " +
              "dark:bg-gray-800 p-3"
            }
          >
            <SimpleBarChart
              categories={activityChart.categories}
              series={activityChart.series}
              title="Recommendations issued vs acted on"
              yAxisLabel="count"
              height={240}
              valueFormatter={(v) => String(v)}
            />
          </div>
          <div
            className={
              "rounded-md border border-gray-200 " +
              "dark:border-gray-700 bg-white " +
              "dark:bg-gray-800 p-3"
            }
          >
            <SimpleBarChart
              categories={hitRateChart.categories}
              series={hitRateChart.series}
              title={`Hit rate at ${horizon}-day horizon`}
              yAxisLabel="%"
              height={280}
              valueFormatter={(v) =>
                `${v.toFixed(1)}%`
              }
            />
          </div>
          <div
            className={
              "rounded-md border border-gray-200 " +
              "dark:border-gray-700 bg-white " +
              "dark:bg-gray-800 p-3"
            }
          >
            <SimpleBarChart
              categories={returnChart.categories}
              series={returnChart.series}
              title={
                "Avg "
                + `${horizon}d return vs benchmark`
              }
              yAxisLabel="%"
              height={280}
              valueFormatter={(v) =>
                `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`
              }
            />
          </div>
        </div>
      )}
    </div>
  );
}
