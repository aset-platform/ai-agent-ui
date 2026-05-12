"use client";
/**
 * Pipeline-assertions section of the admin Data Health panel
 * (ASETPLTFRM-380). Surfaces ``data_quality_violation`` events
 * emitted by the framework — the silent-success runs that
 * previously passed through to dirty outputs (stale VIX, NaN
 * breadth, etc.) now show up here with severity colour-coding.
 *
 * Rendered as a separate section below the main health card grid
 * so it doesn't compete for screen real estate when nothing is
 * wrong (silent on the all-green path).
 */

import { useState } from "react";

import { formatIstDateTime } from "@/lib/datetime";
import {
  usePipelineAssertions,
  type PipelineAssertionRow,
} from "@/hooks/usePipelineAssertions";

type SeverityFilter = "all" | "warn" | "error";

function severityClass(sev: PipelineAssertionRow["severity"]): string {
  return sev === "error"
    ? "bg-rose-100 text-rose-800 dark:bg-rose-950/40 dark:text-rose-300"
    : "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300";
}

function FilterChip({
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
      onClick={onClick}
      className={
        "px-2 py-0.5 text-xs rounded border transition-colors " +
        (active
          ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900 border-slate-900 dark:border-slate-100"
          : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-400 border-slate-300 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800")
      }
    >
      {label}
    </button>
  );
}

export function PipelineAssertionsCard() {
  const [filter, setFilter] = useState<SeverityFilter>("all");
  const { rows, counts, loading } = usePipelineAssertions({
    days: 7,
    severity: filter === "all" ? undefined : filter,
  });

  // Silent on the clean path — no card if there are no violations
  // AND we know the request finished successfully.
  if (!loading && rows.length === 0 && filter === "all") {
    return null;
  }

  return (
    <div
      className="mt-4 rounded-2xl border border-amber-300 dark:border-amber-700 bg-amber-50/40 dark:bg-amber-950/20 p-5"
      data-testid="pipeline-assertions-card"
    >
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h3 className="text-[15px] font-bold">
            Pipeline assertions (last 7 days)
          </h3>
          {(counts.error ?? 0) > 0 && (
            <span className="px-2 py-0.5 text-xs rounded-full bg-rose-100 text-rose-800 dark:bg-rose-950/40 dark:text-rose-300 font-medium">
              {counts.error} error{(counts.error ?? 0) === 1 ? "" : "s"}
            </span>
          )}
          {(counts.warn ?? 0) > 0 && (
            <span className="px-2 py-0.5 text-xs rounded-full bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300 font-medium">
              {counts.warn} warning{(counts.warn ?? 0) === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <div className="flex gap-1.5">
          <FilterChip
            label="All"
            active={filter === "all"}
            onClick={() => setFilter("all")}
          />
          <FilterChip
            label="Errors"
            active={filter === "error"}
            onClick={() => setFilter("error")}
          />
          <FilterChip
            label="Warnings"
            active={filter === "warn"}
            onClick={() => setFilter("warn")}
          />
        </div>
      </div>

      {loading && rows.length === 0 ? (
        <p className="text-xs text-slate-500">Loading violations…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-slate-500">
          No assertion failures in the selected window.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
              <tr>
                <th className="py-1.5 pr-3 font-medium">When (IST)</th>
                <th className="py-1.5 pr-3 font-medium">Pipeline</th>
                <th className="py-1.5 pr-3 font-medium">Step</th>
                <th className="py-1.5 pr-3 font-medium">Assertion</th>
                <th className="py-1.5 pr-3 font-medium">Severity</th>
                <th className="py-1.5 pr-3 font-medium">Message</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={`${r.ts_ns}-${i}`}
                  className="border-b border-slate-100 dark:border-slate-800 last:border-b-0"
                >
                  <td className="py-1.5 pr-3 font-mono text-slate-600 dark:text-slate-400 whitespace-nowrap">
                    {formatIstDateTime(
                      Math.floor(r.ts_ns / 1_000_000),
                    )}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-slate-700 dark:text-slate-300">
                    {r.pipeline_id ?? "—"}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-slate-700 dark:text-slate-300">
                    {r.step ?? "—"}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-slate-700 dark:text-slate-300">
                    {r.assertion ?? "—"}
                  </td>
                  <td className="py-1.5 pr-3">
                    <span
                      className={
                        "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase " +
                        severityClass(r.severity)
                      }
                    >
                      {r.severity}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-slate-700 dark:text-slate-300 max-w-[480px] truncate">
                    {r.message ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
