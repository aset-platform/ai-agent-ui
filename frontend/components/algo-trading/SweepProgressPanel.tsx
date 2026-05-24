"use client";

import { useSweepRun } from "@/hooks/useSweepRuns";

interface Props {
  sweepRunId: string;
}

export function SweepProgressPanel({ sweepRunId }: Props) {
  const { run, isLoading } = useSweepRun(sweepRunId);

  if (isLoading || !run) {
    return (
      <div
        className="rounded-md border p-4 text-sm text-slate-500"
        data-testid="sweep-progress-panel"
      >
        Starting…
      </div>
    );
  }

  const sweptValues = run.swept_values ?? [];
  const total = sweptValues.length;
  const completed = run.variants.filter(
    (v) => v.status === "completed",
  ).length;
  const failed = run.variants.filter(
    (v) => v.status === "failed",
  ).length;
  const pct = total > 0
    ? Math.round((completed / total) * 100)
    : 0;

  return (
    <div
      className="rounded-md border p-4 space-y-3"
      data-testid="sweep-progress-panel"
    >
      <div className="text-sm font-medium">
        Sweep in progress —
        {" "}{completed} of {total} variants complete
        {failed > 0 && (
          <span className="ml-1 text-rose-600">
            ({failed} failed)
          </span>
        )}
      </div>
      <div className="h-2 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden">
        <div
          className="h-full bg-indigo-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <ul className="text-xs space-y-1">
        {sweptValues.map((v, i) => {
          const variant = run.variants[i];
          if (!variant) {
            return (
              <li key={i} className="text-slate-400">
                ⏸ value={String(v)} (queued)
              </li>
            );
          }
          if (variant.status === "completed") {
            return (
              <li
                key={i}
                className="text-emerald-700 dark:text-emerald-300"
                data-testid={`sweep-variant-row-${i}`}
              >
                ✅ value={String(v)}: PnL=
                {variant.avg_pnl_pct}% DD=
                {variant.avg_max_drawdown_pct}%
              </li>
            );
          }
          if (variant.status === "failed") {
            return (
              <li
                key={i}
                className="text-rose-700 dark:text-rose-300"
                data-testid={`sweep-variant-row-${i}`}
              >
                ❌ value={String(v)} ({
                  variant.error_text ?? "failed"
                })
              </li>
            );
          }
          return (
            <li
              key={i}
              className="text-indigo-600 dark:text-indigo-400"
              data-testid={`sweep-variant-row-${i}`}
            >
              ⏳ value={String(v)} (running)
            </li>
          );
        })}
      </ul>
    </div>
  );
}
