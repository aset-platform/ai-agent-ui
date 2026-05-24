"use client";

import type { SweepResult } from "@/lib/types/algoSweep";

interface Props { run: SweepResult; }

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function SweepEquityCurves(_props: Props) {
  // For v1: overlaid equity curves require fetching each
  // variant's child walkforward row + reconstructing the
  // per-day curve. That's a v2 polish. The variant table's
  // "View →" links give users per-variant equity curves
  // immediately via the existing walk-forward UI.
  return (
    <div
      className="rounded-md border p-4 text-xs text-slate-500"
      data-testid="sweep-equity-curves"
    >
      Overlaid equity curves coming in v2. For now, click
      &quot;View →&quot; on any variant row to see its
      walk-forward equity curve.
    </div>
  );
}
