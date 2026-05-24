"use client";

import type { SweepResult } from "@/lib/types/algoSweep";

interface Props { run: SweepResult; }

export function SweepPboBadge({ run }: Props) {
  const pbo = run.cross_variant_pbo;
  const T = run.returns_matrix_shape[0];
  const N = run.returns_matrix_shape[1];

  let verdict: string;
  let tone: "good" | "warn" | "bad" | "muted";
  if (pbo == null) {
    verdict = "N/A — too few common days or variants";
    tone = "muted";
  } else {
    const p = Number(pbo);
    if (p <= 0.30) {
      verdict = (
        "ROBUST. The rank-1 variant tends to also win "
        + "out-of-sample. Promotion is supported."
      );
      tone = "good";
    } else if (p <= 0.50) {
      verdict = (
        "AT-RISK. The rank-1 in-sample winner is partly "
        + "luck. Corroborate with a longer period before "
        + "promoting."
      );
      tone = "warn";
    } else {
      verdict = (
        "LIKELY OVERFIT. The in-sample winner regularly "
        + "underperforms out-of-sample. Don't pick by this "
        + "sweep alone."
      );
      tone = "bad";
    }
  }

  const toneClass = {
    good: "border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30",
    warn: "border-amber-500 bg-amber-50 dark:bg-amber-950/30",
    bad: "border-rose-500 bg-rose-50 dark:bg-rose-950/30",
    muted: "border-slate-300 bg-slate-50 dark:bg-slate-800",
  }[tone];

  return (
    <div
      className={`rounded-md border p-4 ${toneClass}`}
      data-testid="sweep-pbo-badge"
    >
      <div className="text-sm font-medium">
        Cross-variant PBO
      </div>
      <p className="text-lg font-semibold mt-1">
        PBO = {pbo ?? "N/A"}
        <span className="ml-2 text-xs text-slate-500">
          ({T} days × {N} variants)
        </span>
      </p>
      <p className="text-xs mt-1">{verdict}</p>
    </div>
  );
}
