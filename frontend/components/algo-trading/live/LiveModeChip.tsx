"use client";

interface Props {
  mode: "live" | "dry_run";
  armed: boolean;
}

/**
 * Rose / slate / amber chip rendered in the Live page header strip.
 *
 * - "live" + armed  → rose-600  · "LIVE ARMED"
 * - "live" + !armed → slate-400 · "LIVE DISARMED"
 * - "dry_run"       → amber-500 · "DRY-RUN" (defensive — Live page
 *   never expects dry-run, but the runtime might still be in
 *   rehearsal mode and the chip surfaces it).
 */
export function LiveModeChip({ mode, armed }: Props) {
  let label: string;
  let bg: string;
  if (mode === "dry_run") {
    label = "DRY-RUN";
    bg = "bg-amber-500";
  } else if (armed) {
    label = "LIVE ARMED";
    bg = "bg-rose-600";
  } else {
    label = "LIVE DISARMED";
    bg = "bg-slate-400";
  }
  return (
    <span
      data-testid="live-mode-chip"
      className={`inline-flex items-center rounded-full ${bg} px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-white`}
    >
      {label}
    </span>
  );
}
