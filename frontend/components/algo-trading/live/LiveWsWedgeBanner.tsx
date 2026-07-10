"use client";

/**
 * LiveWsWedgeBanner — persistent, high-visibility alert shown when
 * a user's live strategy is armed but the Kite WS connection has
 * wedged and repeated auto-rebuild attempts have failed
 * (ASETPLTFRM-470). Deliberately NOT a hover tooltip — the WS health
 * dot (LiveWsHealthDot) already covers the passive/subtle signal;
 * this is the loud one for when auto-recovery hasn't worked.
 */

interface Props {
  armed: boolean;
  wedgeEscalated: boolean;
}

export function LiveWsWedgeBanner({ armed, wedgeEscalated }: Props) {
  if (!armed || !wedgeEscalated) return null;

  return (
    <div
      className="mx-4 mb-3 flex items-start gap-2 rounded-md border
        border-rose-300 bg-rose-50 px-3 py-2
        dark:border-rose-700 dark:bg-rose-950/40"
      data-testid="live-ws-wedge-banner"
      role="alert"
    >
      <span
        className="mt-px text-base leading-none"
        aria-hidden="true"
      >
        🔴
      </span>
      <p className="text-xs text-rose-800 dark:text-rose-200">
        <span className="font-semibold">
          KITE WS CONNECTION WEDGED
        </span>
        {" — the market-data connection has failed to reconnect "}
        {"after repeated attempts. Live strategies are running "}
        {"BLIND (no signals will fire on missing data). A backend "}
        {"restart is likely required — contact the operator."}
      </p>
    </div>
  );
}
