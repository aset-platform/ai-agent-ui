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
        <span
          tabIndex={0}
          role="button"
          aria-label="What does this do?"
          data-testid="regime-applicability-info"
          title={
            "Two independent layers gate a strategy by " +
            "regime — this chip selector is LAYER 1.\n\n" +
            "──────────────────────────────────────\n" +
            "LAYER 1 — Applicable regimes (these chips)\n" +
            "──────────────────────────────────────\n" +
            "Where:  metadata on the strategy row\n" +
            "Scope:  LIVE picker only\n" +
            "Effect: strategy is hidden from the live " +
            "selector when today's regime ∉ this set\n" +
            "Backtest: NOT affected — runs every day\n" +
            "Paper:    NOT affected — runs every day\n" +
            "Default:  empty = regime-agnostic\n\n" +
            "──────────────────────────────────────\n" +
            "LAYER 2 — regime_label in the AST\n" +
            "──────────────────────────────────────\n" +
            "Where:  a compare node inside entry/exit " +
            "conditions in the JSON\n" +
            "Scope:  backtest + paper + live\n" +
            "Effect: hard-gates the rule, e.g.\n" +
            "        regime_label == \"BULL\"  OR\n" +
            "        regime_label == \"SIDEWAYS\"\n" +
            "How to add: edit the JSON pane, or load a " +
            "template that already has it " +
            "(regime_bull_momentum, " +
            "regime_sideways_meanrev_quality).\n\n" +
            "Note: v3 RSI(2) approximates regime via " +
            "numeric proxies (nifty_above_sma200, " +
            "nifty_30d_return_pct) in its entry gate — " +
            "no explicit regime_label node needed."
          }
          className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-slate-300 text-[10px] font-semibold text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700"
        >
          ?
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
        Live picker only — doesn&apos;t change the AST. Empty =
        regime-agnostic. Hover the{" "}
        <span className="font-semibold">?</span> for details.
      </p>
    </div>
  );
}
