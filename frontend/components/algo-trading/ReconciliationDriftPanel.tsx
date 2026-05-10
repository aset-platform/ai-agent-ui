"use client";
/**
 * ReconciliationDriftPanel — V2-3.
 *
 * Chip with drift count. When drifts exist, expands into a
 * list of symbols with colour-coded severity:
 *   - yellow  (≤ 3 consecutive runs): monitoring
 *   - red     (> 3 consecutive runs): critical — V2-5 uses
 *     this threshold to gate the live-mode toggle.
 *
 * Auto-clears (chip disappears) when the drift list is empty.
 * No dismiss button — transparency chip pattern (CLAUDE.md §5.5).
 */

import { useState } from "react";

import { useReconciliation, type DriftRow } from "@/hooks/useReconciliation";

function SeverityBadge({ runs }: { runs: number }) {
  if (runs > 3) {
    return (
      <span className="inline-flex items-center rounded-full bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-800 dark:bg-rose-950/50 dark:text-rose-200">
        {runs} runs
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950/50 dark:text-amber-200">
      {runs} run{runs !== 1 ? "s" : ""}
    </span>
  );
}

function DriftTable({ drifts }: { drifts: DriftRow[] }) {
  return (
    <div
      className="mt-2 overflow-hidden rounded-md border border-rose-200 dark:border-rose-900"
      data-testid="reconciliation-drift-table"
    >
      <table className="min-w-full text-xs">
        <thead className="bg-rose-50 dark:bg-rose-950/30">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-rose-700 dark:text-rose-300">
              Symbol
            </th>
            <th className="px-3 py-2 text-right font-medium text-rose-700 dark:text-rose-300">
              Our qty
            </th>
            <th className="px-3 py-2 text-right font-medium text-rose-700 dark:text-rose-300">
              Broker qty
            </th>
            <th className="px-3 py-2 text-right font-medium text-rose-700 dark:text-rose-300">
              Diff
            </th>
            <th className="px-3 py-2 text-center font-medium text-rose-700 dark:text-rose-300">
              Severity
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rose-100 dark:divide-rose-900/50 bg-white dark:bg-gray-900">
          {drifts.map((d) => (
            <tr key={d.symbol}>
              <td className="px-3 py-1.5 font-mono font-semibold text-gray-900 dark:text-gray-100">
                {d.symbol}
              </td>
              <td className="px-3 py-1.5 text-right text-gray-700 dark:text-gray-300">
                {d.last_diff?.our_qty ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-right text-gray-700 dark:text-gray-300">
                {d.last_diff?.broker_qty ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-right font-medium text-rose-700 dark:text-rose-300">
                {d.last_diff?.diff !== undefined
                  ? `${d.last_diff.diff > 0 ? "+" : ""}${d.last_diff.diff}`
                  : "—"}
              </td>
              <td className="px-3 py-1.5 text-center">
                <SeverityBadge runs={d.consecutive_runs} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReconciliationDriftPanel() {
  const { drifts, loading, error } = useReconciliation();
  const [expanded, setExpanded] = useState(false);

  // Auto-clear: render nothing when no active drifts.
  if (loading || error || drifts.length === 0) {
    return null;
  }

  const criticalCount = drifts.filter(
    (d) => d.consecutive_runs > 3,
  ).length;
  const isAnyCritical = criticalCount > 0;

  return (
    <div
      className="rounded-md border border-rose-200 bg-rose-50 p-3 dark:border-rose-900 dark:bg-rose-950/20"
      data-testid="reconciliation-drift-panel"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
              isAnyCritical
                ? "bg-rose-600 text-white dark:bg-rose-500"
                : "bg-amber-500 text-white dark:bg-amber-600"
            }`}
            data-testid="reconciliation-drift-chip"
          >
            {drifts.length} position drift
            {drifts.length !== 1 ? "s" : ""}
          </span>
          {isAnyCritical && (
            <span className="text-xs text-rose-700 dark:text-rose-300">
              {criticalCount} critical — live mode gated
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-xs text-rose-600 hover:underline dark:text-rose-400"
          data-testid="reconciliation-drift-toggle"
        >
          {expanded ? "Hide" : "Show"}
        </button>
      </div>

      {expanded && <DriftTable drifts={drifts} />}
    </div>
  );
}
