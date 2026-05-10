"use client";
/**
 * Compact regime widget for the Trading tab header (REGIME-1).
 *
 * Shows:
 * - Regime badge (BULL=emerald, SIDEWAYS=slate, BEAR=rose).
 * - VIX gauge with band coloring (calm <16 emerald, normal
 *   16-25 amber, stress >25 rose).
 * - Breadth bar (% above 50d SMA).
 * - HMM stress chip with divergence warning when the rule label
 *   and HMM stress disagree (BULL + stress>=0.6 → caution;
 *   BEAR + stress<=0.2 → possible thaw).
 */

import { useRegimeCurrent } from "@/hooks/useRegime";

const BADGE_BG: Record<string, string> = {
  BULL:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 "
    + "dark:text-emerald-200",
  SIDEWAYS:
    "bg-slate-100 text-slate-800 dark:bg-slate-800 "
    + "dark:text-slate-200",
  BEAR:
    "bg-rose-100 text-rose-800 dark:bg-rose-950/50 "
    + "dark:text-rose-200",
};

function vixBandColor(vix: number): string {
  if (vix < 16) return "text-emerald-600";
  if (vix <= 25) return "text-amber-600";
  return "text-rose-600";
}

function breadthBg(breadth: number): string {
  if (breadth >= 0.55) return "bg-emerald-500";
  if (breadth >= 0.4) return "bg-amber-500";
  return "bg-rose-500";
}

function divergenceWarning(
  rule: string,
  stress: number | null,
): string | null {
  if (stress === null) return null;
  if (rule === "BULL" && stress >= 0.6) {
    return (
      `Rule says BULL, HMM stress ${stress.toFixed(2)} — caution.`
    );
  }
  if (rule === "BEAR" && stress <= 0.2) {
    return (
      `Rule says BEAR, HMM stress ${stress.toFixed(2)} `
      + "— possible thaw."
    );
  }
  return null;
}

export function RegimeWidget() {
  const { current, loading, error } = useRegimeCurrent();

  if (loading) {
    return (
      <span
        className="text-xs text-slate-500"
        data-testid="regime-widget-loading"
      >
        Loading regime…
      </span>
    );
  }

  if (error || !current) {
    return (
      <span
        className="text-xs text-slate-500"
        data-testid="regime-widget-empty"
      >
        Regime: —
      </span>
    );
  }

  const vix =
    typeof current.rule_inputs.vix_close === "number"
      ? (current.rule_inputs.vix_close as number)
      : null;
  const breadth =
    typeof current.rule_inputs.pct_above_50sma === "number"
      ? (current.rule_inputs.pct_above_50sma as number)
      : null;
  const divergence = divergenceWarning(
    current.regime_label,
    current.stress_prob,
  );

  return (
    <div
      className="flex items-center gap-2"
      data-testid="regime-widget"
    >
      <span
        className={
          "rounded-full px-2.5 py-0.5 text-xs font-medium "
          + BADGE_BG[current.regime_label]
        }
        data-testid="regime-badge"
        title={`As of ${current.bar_date}`}
      >
        {current.regime_label}
      </span>
      {vix !== null && (
        <span
          className={`text-xs font-medium ${vixBandColor(vix)}`}
          data-testid="regime-vix-gauge"
          title={`India VIX ${vix.toFixed(2)}`}
        >
          VIX {vix.toFixed(1)}
        </span>
      )}
      {breadth !== null && (
        <span
          className="flex items-center gap-1 text-xs text-slate-600
            dark:text-slate-300"
          data-testid="regime-breadth-bar"
          title={
            `Breadth: ${(breadth * 100).toFixed(0)}% `
            + "above 50d SMA"
          }
        >
          <span className="h-2 w-12 rounded bg-slate-200
            dark:bg-slate-700">
            <span
              className={`block h-2 rounded ${breadthBg(breadth)}`}
              style={{
                width: `${Math.min(100, breadth * 100)}%`,
              }}
            />
          </span>
          {(breadth * 100).toFixed(0)}%
        </span>
      )}
      {current.stress_prob !== null && (
        <span
          className={
            divergence
              ? "rounded bg-amber-100 px-1.5 py-0.5 text-[11px] "
                + "font-medium text-amber-800 "
                + "dark:bg-amber-950/50 dark:text-amber-200"
              : "rounded bg-slate-100 px-1.5 py-0.5 text-[11px] "
                + "text-slate-600 dark:bg-slate-800 "
                + "dark:text-slate-300"
          }
          data-testid="regime-stress-chip"
          title={
            divergence
            ?? `HMM stress ${current.stress_prob.toFixed(2)}`
          }
        >
          stress {current.stress_prob.toFixed(2)}
        </span>
      )}
    </div>
  );
}
