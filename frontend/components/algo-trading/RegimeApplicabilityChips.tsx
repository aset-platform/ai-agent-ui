"use client";
/**
 * REGIME-3 — multi-select regime chips for the strategy editor.
 *
 * Renders three toggleable pills (BULL / SIDEWAYS / BEAR). Empty
 * selection is treated as regime-agnostic by the backend default.
 * Shows an amber inline warning when the *current* market regime
 * is not in the selected set ("strategy will be off-regime today").
 */

import {
  REGIME_LABELS,
  type RegimeLabel,
} from "@/lib/types/algoStrategy";

const ACTIVE_BG: Record<RegimeLabel, string> = {
  bull: "bg-emerald-500 text-white border-emerald-500",
  sideways: "bg-slate-500 text-white border-slate-500",
  bear: "bg-rose-500 text-white border-rose-500",
};

interface Props {
  selected: RegimeLabel[];
  onChange: (next: RegimeLabel[]) => void;
  currentRegime?: RegimeLabel;
  disabled?: boolean;
}

export function RegimeApplicabilityChips({
  selected,
  onChange,
  currentRegime,
  disabled,
}: Props) {
  const toggle = (r: RegimeLabel) => {
    if (disabled) return;
    if (selected.includes(r)) {
      onChange(selected.filter((x) => x !== r));
    } else {
      onChange([...selected, r]);
    }
  };
  const mismatched =
    !!currentRegime &&
    selected.length > 0 &&
    !selected.includes(currentRegime);
  return (
    <div
      data-testid="regime-applicability-chips"
      className="space-y-1"
    >
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wide">
          Applicable regimes
        </span>
        {mismatched && (
          <span
            className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800 dark:bg-amber-950/50 dark:text-amber-200"
            data-testid="regime-applicability-mismatch-warning"
          >
            Current: {currentRegime?.toUpperCase()} — not selected
          </span>
        )}
      </div>
      <div className="flex gap-2">
        {REGIME_LABELS.map((r) => {
          const active = selected.includes(r);
          return (
            <button
              key={r}
              type="button"
              onClick={() => toggle(r)}
              disabled={disabled}
              aria-pressed={active}
              className={
                "rounded-full px-3 py-1 text-xs font-medium border " +
                (active
                  ? ACTIVE_BG[r]
                  : "bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 border-slate-300 dark:border-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700")
              }
              data-testid={`regime-applicability-chip-${r}`}
            >
              {r.toUpperCase()}
            </button>
          );
        })}
      </div>
      <p className="text-[10px] text-slate-400 dark:text-slate-500">
        Empty = regime-agnostic (default). Filtered in the live
        selector by current regime ∩ this set.
      </p>
    </div>
  );
}
