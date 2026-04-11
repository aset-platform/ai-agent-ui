"use client";
/**
 * DataHealthPanel — data quality dashboard with
 * health cards, actionable fix buttons, and
 * remediation suggestions.
 */

import { useState, useCallback } from "react";
import {
  useDataHealth,
  type DataHealthResult,
} from "@/hooks/useAdminData";

type Status = "green" | "yellow" | "red";

function Dot({ s }: { s: Status }) {
  const c =
    s === "green"
      ? "bg-emerald-500"
      : s === "yellow"
        ? "bg-amber-500"
        : "bg-red-500";
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${c}`}
    />
  );
}

function Pill({
  label,
  color,
}: {
  label: string;
  color: "red" | "amber" | "gray";
}) {
  const cls =
    color === "red"
      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
      : color === "amber"
        ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
        : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400";
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${cls}`}
    >
      {label}
    </span>
  );
}

function FixBtn({
  label,
  onClick,
  busy,
  variant = "amber",
}: {
  label: string;
  onClick: () => void;
  busy: boolean;
  variant?: "red" | "amber" | "indigo";
}) {
  const bg =
    variant === "red"
      ? "bg-red-600 hover:bg-red-700"
      : variant === "indigo"
        ? "bg-indigo-600 hover:bg-indigo-700"
        : "bg-amber-600 hover:bg-amber-700";
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`px-2.5 py-1 text-[11px] font-medium rounded-lg text-white ${bg} disabled:opacity-50`}
    >
      {busy ? "Working..." : label}
    </button>
  );
}

function Suggestion({ text }: { text: string }) {
  return (
    <p className="mt-2 text-[10px] text-gray-500 dark:text-gray-500 italic">
      {text}
    </p>
  );
}

function Skeleton() {
  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 animate-pulse">
      <div className="h-4 w-24 bg-gray-200 dark:bg-gray-700 rounded mb-3" />
      <div className="h-3 w-32 bg-gray-200 dark:bg-gray-700 rounded mb-2" />
      <div className="h-3 w-20 bg-gray-200 dark:bg-gray-700 rounded" />
    </div>
  );
}

// ── Cards ──────────────────────────────────────────

function OhlcvCard({
  d,
  total,
  onFix,
  fixing,
}: {
  d: DataHealthResult["ohlcv"];
  total: number;
  onFix: (a: string) => void;
  fixing: string | null;
}) {
  const hasNaN = d.nan_close_count > 0;
  const hasMissing = d.missing_latest_count > 0;
  const hasStale = d.stale_count > 0;
  const status: Status = hasNaN || hasMissing
    ? "red"
    : hasStale
      ? "yellow"
      : "green";

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        <Dot s={status} />
        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          OHLCV Data
        </h4>
      </div>
      <div className="space-y-1 text-xs text-gray-600 dark:text-gray-400 flex-1">
        <p className="font-medium text-gray-900 dark:text-gray-100">
          {total - d.missing_latest_count}/{total}{" "}
          tickers up to date
        </p>
        {hasNaN && (
          <p className="flex items-center gap-1.5">
            <Pill label={`${d.nan_close_count} NaN`} color="red" />
            rows with NULL/NaN close
          </p>
        )}
        {hasMissing && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.missing_latest_count} missing`}
              color="amber"
            />
            tickers without latest date
          </p>
        )}
        {hasStale && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.stale_count} stale`}
              color="amber"
            />
            data older than 3 days
          </p>
        )}
      </div>
      {status !== "green" && (
        <div className="flex flex-wrap gap-2 mt-3">
          {hasNaN && (
            <FixBtn
              label="Clean NaN Rows"
              onClick={() => onFix("backfill_nan")}
              busy={fixing === "backfill_nan"}
              variant="red"
            />
          )}
          {(hasMissing || hasStale) && (
            <FixBtn
              label="Backfill from yfinance"
              onClick={() => onFix("backfill_missing")}
              busy={fixing === "backfill_missing"}
            />
          )}
        </div>
      )}
      {status !== "green" && (
        <Suggestion
          text="After fixing, re-run Data Refresh pipeline with force."
        />
      )}
      {status === "green" && (
        <Suggestion text="All tickers have clean, up-to-date OHLCV data." />
      )}
    </div>
  );
}

function ForecastCard({
  d,
  total,
}: {
  d: DataHealthResult["forecasts"];
  total: number;
}) {
  const missing = total - d.total_tickers;
  const status: Status =
    missing > 50
      ? "red"
      : d.extreme_predictions > 50 || d.high_mape > 100
        ? "yellow"
        : "green";

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        <Dot s={status} />
        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Forecasts
        </h4>
      </div>
      <div className="space-y-1 text-xs text-gray-600 dark:text-gray-400 flex-1">
        <p className="font-medium text-gray-900 dark:text-gray-100">
          {d.total_tickers}/{total} tickers scored
        </p>
        {d.extreme_predictions > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.extreme_predictions} extreme`}
              color="amber"
            />
            predictions &gt;50% deviation
          </p>
        )}
        {d.high_mape > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.high_mape} high MAPE`}
              color="amber"
            />
            accuracy &gt;25%
          </p>
        )}
        {d.stale_count > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.stale_count} stale`}
              color="amber"
            />
            older than 30 days
          </p>
        )}
      </div>
      {status !== "green" ? (
        <Suggestion text="Run Forecast pipeline with force=true to recompute. Extreme predictions need model tuning." />
      ) : (
        <Suggestion text="All forecasts are fresh and within normal range." />
      )}
    </div>
  );
}

function SentimentCard({
  d,
  total,
}: {
  d: DataHealthResult["sentiment"];
  total: number;
}) {
  const missing = total - d.total_tickers;
  const status: Status =
    missing > 10 || d.stale_count > 50
      ? "yellow"
      : "green";

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        <Dot s={status} />
        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Sentiment
        </h4>
      </div>
      <div className="space-y-1 text-xs text-gray-600 dark:text-gray-400 flex-1">
        <p className="font-medium text-gray-900 dark:text-gray-100">
          {d.total_tickers}/{total} tickers scored
        </p>
        {d.missing_tickers.length > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.missing_tickers.length} missing`}
              color="amber"
            />
          </p>
        )}
        {d.stale_count > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.stale_count} stale`}
              color="amber"
            />
            older than 7 days
          </p>
        )}
      </div>
      {status !== "green" ? (
        <Suggestion text="Run Sentiment pipeline to refresh scores." />
      ) : (
        <Suggestion text="All sentiment scores are up to date." />
      )}
    </div>
  );
}

function PiotroskiCard({
  d,
  total,
}: {
  d: DataHealthResult["piotroski"];
  total: number;
}) {
  const status: Status =
    d.missing_tickers.length > 10
      ? "yellow"
      : "green";

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        <Dot s={status} />
        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Piotroski F-Score
        </h4>
      </div>
      <div className="space-y-1 text-xs text-gray-600 dark:text-gray-400 flex-1">
        <p className="font-medium text-gray-900 dark:text-gray-100">
          {d.total_tickers}/{total} tickers scored
        </p>
        {d.missing_tickers.length > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.missing_tickers.length} missing`}
              color="amber"
            />
            {d.missing_tickers.length <= 5 &&
              d.missing_tickers.join(", ")}
          </p>
        )}
        {d.stale_count > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.stale_count} stale`}
              color="amber"
            />
            older than 30 days
          </p>
        )}
      </div>
      {status !== "green" ? (
        <Suggestion text="Run Piotroski pipeline to score missing tickers." />
      ) : (
        <Suggestion text="All Piotroski scores are current." />
      )}
    </div>
  );
}

function AnalyticsCard({
  d,
  total,
}: {
  d: DataHealthResult["analytics"];
  total: number;
}) {
  const status: Status =
    d.missing_tickers.length > 10
      ? "yellow"
      : "green";

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        <Dot s={status} />
        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Analytics
        </h4>
      </div>
      <div className="space-y-1 text-xs text-gray-600 dark:text-gray-400 flex-1">
        <p className="font-medium text-gray-900 dark:text-gray-100">
          {d.total_tickers}/{total} tickers computed
        </p>
        {d.missing_tickers.length > 0 && (
          <p className="flex items-center gap-1.5">
            <Pill
              label={`${d.missing_tickers.length} missing`}
              color="amber"
            />
          </p>
        )}
      </div>
      {status !== "green" ? (
        <Suggestion text="Run Compute Analytics pipeline to fill gaps." />
      ) : (
        <Suggestion text="All analytics summaries are computed." />
      )}
    </div>
  );
}

// ── Main Panel ─────────────────────────────────────

export function DataHealthPanel() {
  const { data, loading, error, refresh, fixOhlcv } =
    useDataHealth();
  const [fixing, setFixing] = useState<string | null>(
    null,
  );

  const handleFix = useCallback(
    async (action: string) => {
      setFixing(action);
      try {
        await fixOhlcv(
          action as
            | "backfill_nan"
            | "backfill_missing",
        );
        refresh();
      } catch {
        /* error surfaced on re-scan */
      }
      setFixing(null);
    },
    [fixOhlcv, refresh],
  );

  if (error) {
    return (
      <div className="rounded-2xl border border-red-200 dark:border-red-800 bg-white dark:bg-gray-900/80 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-[15px] font-bold text-red-700 dark:text-red-400">
              Data Health Scan Failed
            </h3>
            <p className="text-xs text-red-600 dark:text-red-400 mt-1">
              {error}
            </p>
          </div>
          <button
            onClick={refresh}
            className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const total = data?.total_registry ?? 0;

  return (
    <div className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/80 p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[15px] font-bold">
          Data Health
        </h3>
        <button
          onClick={refresh}
          disabled={loading}
          className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
        >
          {loading ? "Scanning..." : "Re-scan"}
        </button>
      </div>

      {loading && !data ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} />
          ))}
        </div>
      ) : data ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
          <OhlcvCard
            d={data.ohlcv}
            total={total}
            onFix={handleFix}
            fixing={fixing}
          />
          <AnalyticsCard
            d={data.analytics}
            total={total}
          />
          <SentimentCard
            d={data.sentiment}
            total={total}
          />
          <PiotroskiCard
            d={data.piotroski}
            total={total}
          />
          <ForecastCard
            d={data.forecasts}
            total={total}
          />
        </div>
      ) : null}
    </div>
  );
}
