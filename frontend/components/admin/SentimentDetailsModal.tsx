"use client";
/**
 * Data-health → Sentiment → "View details" modal.
 *
 * Shows today's sentiment scoring breakdown:
 *  - by-source tiles: finbert / llm / market_fallback / none
 *  - filterable list of tickers scored from real
 *    headlines (fallback rows excluded).
 */

import {
  useEffect,
  useMemo,
  useState,
} from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { downloadCsv } from "@/lib/downloadCsv";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";

interface BySource {
  source: string;
  count: number;
  avg_score: number;
}

interface ScoredRow {
  ticker: string;
  score: number;
  headline_count: number;
  source: string;
}

interface DetailsPayload {
  by_source: BySource[];
  scored: ScoredRow[];
  total: number;
  scope: string;
  as_of: string;
}

type Scope = "all" | "india" | "us";

const SOURCE_META: Record<
  string,
  { label: string; color: string }
> = {
  finbert: {
    label: "FinBERT",
    color:
      "bg-indigo-100 text-indigo-700 ring-indigo-200 " +
      "dark:bg-indigo-900/30 dark:text-indigo-300 " +
      "dark:ring-indigo-800",
  },
  llm: {
    label: "LLM",
    color:
      "bg-violet-100 text-violet-700 ring-violet-200 " +
      "dark:bg-violet-900/30 dark:text-violet-300 " +
      "dark:ring-violet-800",
  },
  market_fallback: {
    label: "Market fallback",
    color:
      "bg-amber-100 text-amber-700 ring-amber-200 " +
      "dark:bg-amber-900/30 dark:text-amber-300 " +
      "dark:ring-amber-800",
  },
  none: {
    label: "None",
    color:
      "bg-gray-100 text-gray-600 ring-gray-200 " +
      "dark:bg-gray-800 dark:text-gray-400 " +
      "dark:ring-gray-700",
  },
};

function sourceMeta(src: string) {
  return (
    SOURCE_META[src] ?? {
      label: src,
      color:
        "bg-gray-100 text-gray-600 ring-gray-200 " +
        "dark:bg-gray-800 dark:text-gray-400 " +
        "dark:ring-gray-700",
    }
  );
}

function fmtScore(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(3)}`;
}

export function SentimentDetailsModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [scope, setScope] = useState<Scope>("all");
  const [data, setData] =
    useState<DetailsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(
    null,
  );
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  // Reset to page 1 whenever the filter or scope
  // changes — otherwise a narrowed list can leave
  // the page index past the last page.
  useEffect(() => {
    setPage(1);
  }, [filter, scope]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setError(null);
    apiFetch(
      `${API_URL}/admin/data-health/sentiment-details?scope=${scope}`,
    )
      .then(async (r) => {
        if (!alive) return;
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        const body =
          (await r.json()) as DetailsPayload;
        if (alive) setData(body);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(
          e instanceof Error
            ? e.message
            : "Failed to load",
        );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, scope]);

  // Escape to close
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", h);
    return () =>
      document.removeEventListener("keydown", h);
  }, [open, onClose]);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = filter.trim().toUpperCase();
    if (!q) return data.scored;
    return data.scored.filter(
      (r) =>
        r.ticker.toUpperCase().includes(q) ||
        r.source.toUpperCase().includes(q),
    );
  }, [data, filter]);

  const maxPages = Math.max(
    1,
    Math.ceil(filtered.length / pageSize),
  );
  const safePage = Math.min(page, maxPages);
  const paged = useMemo(
    () =>
      filtered.slice(
        (safePage - 1) * pageSize,
        safePage * pageSize,
      ),
    [filtered, safePage, pageSize],
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-3xl max-h-[85vh] overflow-hidden flex flex-col rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-xl"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              Sentiment scoring details
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {data
                ? `${data.total} rows for ${data.as_of}`
                : "Loading…"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-md border border-gray-200 dark:border-gray-700 overflow-hidden text-[11px]">
              {(["all", "india", "us"] as const).map(
                (s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setScope(s)}
                    className={
                      "px-2.5 py-1 font-medium uppercase " +
                      (scope === s
                        ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                        : "bg-white text-gray-600 hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700")
                    }
                  >
                    {s}
                  </button>
                ),
              )}
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
        </div>

        {/* Body */}
        <div className="overflow-y-auto px-5 py-4 space-y-5">
          {loading && (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Loading…
            </p>
          )}
          {error && (
            <p className="text-sm text-red-600 dark:text-red-400">
              {error}
            </p>
          )}

          {data && !loading && !error && (
            <>
              {/* Source tiles */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {data.by_source.length === 0 ? (
                  <p className="col-span-full text-sm text-gray-500 dark:text-gray-400">
                    No sentiment rows for {data.scope}{" "}
                    today.
                  </p>
                ) : (
                  data.by_source.map((b) => {
                    const meta = sourceMeta(b.source);
                    return (
                      <div
                        key={b.source}
                        className={
                          "rounded-xl p-3 ring-1 " +
                          meta.color
                        }
                      >
                        <p className="text-[10px] font-semibold uppercase tracking-wide opacity-80">
                          {meta.label}
                        </p>
                        <p className="text-2xl font-semibold mt-1">
                          {b.count}
                        </p>
                        <p className="text-[11px] mt-1 opacity-80">
                          avg {fmtScore(b.avg_score)}
                        </p>
                      </div>
                    );
                  })
                )}
              </div>

              {/* Ticker list */}
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
                    Scored tickers
                    <span className="ml-2 text-xs font-normal text-gray-500 dark:text-gray-400">
                      ({filtered.length} of{" "}
                      {data.scored.length})
                    </span>
                  </h4>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={filter}
                      onChange={(e) =>
                        setFilter(e.target.value)
                      }
                      placeholder="Filter by ticker or source…"
                      className="w-56 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-xs text-gray-900 dark:text-gray-100 placeholder:text-gray-400 focus:border-indigo-500 focus:outline-none"
                    />
                    <DownloadCsvButton
                      onClick={() => {
                        if (!filtered.length) return;
                        downloadCsv(
                          filtered,
                          [
                            {
                              key: "ticker",
                              header: "Ticker",
                            },
                            {
                              key: "score",
                              header: "Score",
                            },
                            {
                              key: "headline_count",
                              header: "Headlines",
                            },
                            {
                              key: "source",
                              header: "Source",
                            },
                          ],
                          `sentiment-scores-${data.scope}-${data.as_of}`,
                        );
                      }}
                      disabled={filtered.length === 0}
                    />
                  </div>
                </div>

                {filtered.length === 0 ? (
                  <p className="text-sm text-gray-500 dark:text-gray-400 py-6 text-center">
                    No rows.{" "}
                    {data.scored.length === 0
                      ? "No tickers produced headlines today — everyone got the market fallback."
                      : "Nothing matches the filter."}
                  </p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="bg-gray-50 dark:bg-gray-800/50 text-gray-600 dark:text-gray-400">
                          <th className="px-3 py-2 text-left font-medium">
                            Ticker
                          </th>
                          <th className="px-3 py-2 text-right font-medium">
                            Score
                          </th>
                          <th className="px-3 py-2 text-right font-medium">
                            Headlines
                          </th>
                          <th className="px-3 py-2 text-left font-medium">
                            Source
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                        {paged.map((r) => {
                          const meta = sourceMeta(
                            r.source,
                          );
                          const scoreColor =
                            r.score > 0.1
                              ? "text-emerald-600 dark:text-emerald-400"
                              : r.score < -0.1
                                ? "text-red-600 dark:text-red-400"
                                : "text-gray-600 dark:text-gray-300";
                          return (
                            <tr
                              key={r.ticker}
                              className="hover:bg-gray-50 dark:hover:bg-gray-800/40"
                            >
                              <td className="px-3 py-1.5 font-mono font-semibold text-gray-900 dark:text-gray-100">
                                {r.ticker}
                              </td>
                              <td
                                className={
                                  "px-3 py-1.5 text-right tabular-nums font-medium " +
                                  scoreColor
                                }
                              >
                                {fmtScore(r.score)}
                              </td>
                              <td className="px-3 py-1.5 text-right tabular-nums text-gray-600 dark:text-gray-300">
                                {r.headline_count}
                              </td>
                              <td className="px-3 py-1.5">
                                <span
                                  className={
                                    "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 " +
                                    meta.color
                                  }
                                >
                                  {meta.label}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                {filtered.length > 0 && (
                  <div className="flex flex-wrap items-center justify-between gap-3 pt-2 text-xs text-gray-600 dark:text-gray-400">
                    <div className="flex items-center gap-2">
                      <span>
                        {filtered.length} scored
                      </span>
                      <select
                        value={pageSize}
                        onChange={(e) => {
                          setPageSize(
                            Number(e.target.value),
                          );
                          setPage(1);
                        }}
                        className="rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-xs text-gray-900 dark:text-gray-100"
                      >
                        {[10, 25, 50, 100].map((n) => (
                          <option key={n} value={n}>
                            {n}/page
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          setPage((p) =>
                            Math.max(1, p - 1),
                          )
                        }
                        disabled={safePage <= 1}
                        className="rounded-md border border-gray-300 dark:border-gray-600 px-2 py-1 font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-40"
                      >
                        Prev
                      </button>
                      <span className="tabular-nums">
                        {safePage} / {maxPages}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          setPage((p) =>
                            Math.min(
                              maxPages, p + 1,
                            ),
                          )
                        }
                        disabled={
                          safePage >= maxPages
                        }
                        className="rounded-md border border-gray-300 dark:border-gray-600 px-2 py-1 font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-40"
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
