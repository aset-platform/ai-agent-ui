"use client";

/**
 * LiveDryRunBanner — amber warning shown when the backend is
 * operating in dry-run mode (ALGO_LIVE_DRY_RUN=true).
 *
 * Renders nothing when dry_run is false or when gates data
 * is not yet loaded.
 */

import type { GatesStatus } from "@/hooks/useLiveStatus";

interface Props {
  gates: GatesStatus | null;
}

export function LiveDryRunBanner({ gates }: Props) {
  if (!gates?.dry_run) return null;

  return (
    <div
      className="flex items-start gap-2 rounded-md border
        border-amber-300 bg-amber-50 px-3 py-2
        dark:border-amber-700 dark:bg-amber-950/40"
      data-testid="live-dry-run-banner"
      role="alert"
    >
      <span
        className="mt-px text-base leading-none"
        aria-hidden="true"
      >
        🟡
      </span>
      <p className="text-xs text-amber-800 dark:text-amber-200">
        <span className="font-semibold">DRY RUN MODE</span>
        {" — orders will be simulated. No real Kite calls are made. "}
        Set{" "}
        <code className="rounded bg-amber-100 px-1
          dark:bg-amber-900/60 font-mono text-[11px]">
          ALGO_LIVE_DRY_RUN=false
        </code>{" "}
        and restart the backend to switch to real orders.
      </p>
    </div>
  );
}
