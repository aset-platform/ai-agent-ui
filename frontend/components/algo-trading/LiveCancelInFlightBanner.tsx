"use client";
/**
 * LiveCancelInFlightBanner — V2-5.
 *
 * When the kill switch is ARMED, shows an amber warning banner
 * above the in-flight orders list explaining that new orders are
 * blocked.  Positions are NOT affected (spec §13 risk #5).
 *
 * This component is purely informational — the actual kill-switch
 * control lives in KillSwitchToggle (Settings tab).
 */

import { useKillSwitch } from "@/hooks/useKillSwitch";

interface Props {
  /** If true, live orders mode is currently enabled. */
  liveEnabled: boolean;
}

export function LiveCancelInFlightBanner({ liveEnabled }: Props) {
  const { state } = useKillSwitch();
  const killActive = state?.active ?? false;

  if (!liveEnabled) return null;
  if (!killActive) return null;

  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50
        px-3 py-2 text-sm text-amber-800
        dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-200"
      role="alert"
      data-testid="live-kill-switch-banner"
    >
      <span className="font-semibold">Kill switch is ARMED.</span>
      {" "}New live orders are blocked. In-flight orders were
      cancelled at kill time. Open positions are unaffected —
      manage them manually via your broker app.
      {state?.reason && (
        <span className="ml-1 italic">
          Reason: {state.reason}
        </span>
      )}
    </div>
  );
}
