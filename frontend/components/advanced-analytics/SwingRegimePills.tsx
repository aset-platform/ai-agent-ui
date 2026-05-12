"use client";

/**
 * Bull / Sideways / Bearish segmented control for the
 * Swing Setups tab. Renders three button pills with the
 * active one visually highlighted.
 */

import type { SwingRegime } from "@/lib/types/swingSetups";

const REGIMES: { value: SwingRegime; label: string }[] = [
  { value: "bull", label: "Bull" },
  { value: "sideways", label: "Sideways" },
  { value: "bearish", label: "Bearish" },
];

interface Props {
  value: SwingRegime;
  onChange: (regime: SwingRegime) => void;
}

export function SwingRegimePills({ value, onChange }: Props) {
  return (
    <div
      role="tablist"
      aria-label="Swing regime"
      className="inline-flex rounded-md border bg-slate-100 dark:bg-slate-800 p-1"
      data-testid="swing-regime-pills"
    >
      {REGIMES.map((r) => {
        const active = value === r.value;
        return (
          <button
            key={r.value}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={`swing-regime-pill-${r.value}`}
            onClick={() => onChange(r.value)}
            className={
              "px-3 py-1.5 text-sm rounded-sm transition "
              + (active
                ? "bg-white dark:bg-slate-900 text-slate-900 "
                + "dark:text-slate-100 shadow font-medium"
                : "text-slate-600 dark:text-slate-300 "
                + "hover:text-slate-900 dark:hover:text-slate-100")
            }
          >
            {r.label}
          </button>
        );
      })}
    </div>
  );
}
