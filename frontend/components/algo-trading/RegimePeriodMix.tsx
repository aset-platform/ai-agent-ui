"use client";
/**
 * RegimePeriodMix — backtest tab helper.
 *
 * Given a (start, end) date range, fetches /v1/algo/regime/
 * period-summary and renders:
 *   • Three coloured bars (BULL / SIDEWAYS / BEAR) with %.
 *   • The dominant-regime chip with the matching template name
 *     when one regime is ≥ 50% of the period.
 *   • Avg HMM stress for the period.
 *
 * Read-only — does not modify form state. Refreshes via SWR on
 * any (start, end) change.
 */

import { useRegimePeriodSummary } from "@/hooks/useRegime";

const REGIME_BAR: Record<string, string> = {
  BULL: "bg-emerald-500",
  SIDEWAYS: "bg-slate-500",
  BEAR: "bg-rose-500",
};
const REGIME_TEXT: Record<string, string> = {
  BULL: "text-emerald-700 dark:text-emerald-300",
  SIDEWAYS: "text-slate-700 dark:text-slate-300",
  BEAR: "text-rose-700 dark:text-rose-300",
};
const TEMPLATE_DISPLAY: Record<string, string> = {
  regime_bull_momentum: "BULL — Momentum + Trend",
  regime_sideways_meanrev_quality:
    "SIDEWAYS — Mean Reversion + Quality",
  regime_bear_defensive_lowvol:
    "BEAR — Defensive Low-Vol Quality",
};

interface Props {
  start: string;
  end: string;
}

function stressBand(p: number | null): string {
  if (p === null) return "—";
  if (p < 0.3) return "Calm";
  if (p < 0.6) return "Transitional";
  if (p < 0.8) return "Stressed";
  return "High stress";
}

export function RegimePeriodMix({ start, end }: Props) {
  const { summary, loading, error } = useRegimePeriodSummary(
    start, end,
  );

  if (!start || !end) return null;
  if (loading && !summary) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="regime-period-mix-loading"
      >
        Computing regime mix for the selected period…
      </p>
    );
  }
  if (error) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="regime-period-mix-error"
      >
        Could not load regime mix.
      </p>
    );
  }
  if (!summary || summary.total_days === 0) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="regime-period-mix-empty"
      >
        No regime history for this period yet — backfill needed
        before the recommendation can be made.
      </p>
    );
  }

  const order = ["BULL", "SIDEWAYS", "BEAR"];

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-3 space-y-2"
      data-testid="regime-period-mix"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
          Regime mix for {start} → {end}
        </span>
        <span className="text-[10px] text-slate-500">
          {summary.total_days} regime-classified days
        </span>
      </div>

      {/* Stacked horizontal bar */}
      <div
        className="flex h-2 w-full overflow-hidden rounded bg-slate-200 dark:bg-slate-800"
        data-testid="regime-period-mix-bar"
      >
        {order.map((regime) => {
          const pct = summary.pct[regime] ?? 0;
          if (pct <= 0) return null;
          return (
            <div
              key={regime}
              className={REGIME_BAR[regime]}
              style={{ width: `${pct}%` }}
              title={`${regime}: ${pct.toFixed(1)}%`}
            />
          );
        })}
      </div>

      {/* Per-regime breakdown */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
        {order.map((regime) => (
          <span
            key={regime}
            className={`flex items-center gap-1 ${REGIME_TEXT[regime]}`}
            data-testid={`regime-period-mix-${regime.toLowerCase()}`}
          >
            <span
              className={`inline-block h-2 w-2 rounded-full ${REGIME_BAR[regime]}`}
            />
            <span className="font-medium">{regime}</span>
            <span>{(summary.pct[regime] ?? 0).toFixed(1)}%</span>
            <span className="text-slate-400">
              ({summary.counts[regime] ?? 0}d)
            </span>
          </span>
        ))}
        <span className="ml-auto text-slate-500">
          Avg stress: <strong>
            {summary.avg_stress_prob == null
              ? "—"
              : summary.avg_stress_prob.toFixed(2)}
          </strong>
          {" "}
          <span className="text-[10px]">
            ({stressBand(summary.avg_stress_prob)})
          </span>
        </span>
      </div>

      {/* Recommendation */}
      {summary.recommended_template ? (
        <div
          className="rounded bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800 px-2.5 py-1.5 text-[11px] text-indigo-900 dark:text-indigo-200"
          data-testid="regime-period-mix-recommendation"
        >
          <strong>Recommended template:</strong>{" "}
          {TEMPLATE_DISPLAY[summary.recommended_template]
            ?? summary.recommended_template}
          {" "}
          — {summary.dominant} dominates this period.
        </div>
      ) : (
        <div
          className="text-[11px] text-slate-500"
          data-testid="regime-period-mix-no-recommendation"
        >
          No regime is ≥ 50% of the period — try all three
          regime templates and compare.
        </div>
      )}
    </div>
  );
}
