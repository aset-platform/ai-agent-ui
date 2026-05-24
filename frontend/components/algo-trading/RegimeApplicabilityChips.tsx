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
import { InfoTooltip } from "@/components/common/InfoTooltip";

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
        <InfoTooltip
          label="How regime filters work"
          widthClass="w-80"
        >
          <span className="whitespace-pre-line">
            {"Two independent layers gate a strategy by " +
              "regime — these chips are LAYER 1.\n\n" +
              "LAYER 1 — Applicable regimes (these " +
              "chips)\n" +
              "  • Stored as metadata on the strategy\n" +
              "  • LIVE picker only — hidden from live " +
              "selector when today's regime ∉ this set\n" +
              "  • Backtest + paper: NOT affected\n" +
              "  • Empty = regime-agnostic (default)\n\n" +
              "LAYER 2 — regime_label in the AST\n" +
              "  • Compare node inside entry/exit JSON\n" +
              "  • Scope: backtest + paper + live\n" +
              "  • Hard-gates the rule, e.g.\n" +
              "      regime_label == \"BULL\"  OR\n" +
              "      regime_label == \"SIDEWAYS\"\n" +
              "  • Add via JSON pane or a regime-* " +
              "template\n\n" +
              "v3 RSI(2) note: approximates regime via " +
              "numeric proxies (nifty_above_sma200, " +
              "nifty_30d_return_pct) in its entry gate — " +
              "no explicit regime_label node needed."}
          </span>
        </InfoTooltip>
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
        Live picker only — doesn&apos;t change the AST.
        Empty = regime-agnostic. Hover the{" "}
        <span className="font-semibold">i</span> for details.
      </p>
    </div>
  );
}
