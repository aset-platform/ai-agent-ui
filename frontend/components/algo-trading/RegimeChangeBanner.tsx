"use client";
/**
 * REGIME-3 — amber banner for regime flips.
 *
 * Driven by `useRegimeCurrent` (60s polling). Compares the current
 * regime label against `localStorage.getItem("algo.regime.lastSeen")`.
 * On mismatch shows the banner; on dismiss writes
 * `algo.regime.dismissed.<regime>` with a 4-hour expiry timestamp so
 * the banner stays hidden through the trading session.
 *
 * SSR-safe: localStorage access only inside `useEffect`.
 */

import { useEffect, useRef, useState } from "react";

import { useRegimeCurrent } from "@/hooks/useRegime";

const LAST_SEEN_KEY = "algo.regime.lastSeen";
const DISMISS_KEY_PREFIX = "algo.regime.dismissed.";
const DISMISS_TTL_MS = 4 * 60 * 60 * 1000; // 4 hours

interface BannerState {
  from: string;
  to: string;
}

function _computeBannerState(
  regimeLabel: string,
): BannerState | null {
  if (typeof window === "undefined") return null;
  const lastSeen = localStorage.getItem(LAST_SEEN_KEY);
  if (!lastSeen || lastSeen === regimeLabel) {
    localStorage.setItem(LAST_SEEN_KEY, regimeLabel);
    return null;
  }
  const dismissedTs = localStorage.getItem(
    DISMISS_KEY_PREFIX + regimeLabel,
  );
  // Always advance lastSeen so a subsequent flip-back also fires.
  localStorage.setItem(LAST_SEEN_KEY, regimeLabel);
  if (
    dismissedTs &&
    Date.now() - parseInt(dismissedTs, 10) < DISMISS_TTL_MS
  ) {
    return null;
  }
  return { from: lastSeen, to: regimeLabel };
}

export function RegimeChangeBanner() {
  const { current } = useRegimeCurrent();
  const [banner, setBanner] = useState<BannerState | null>(null);
  const lastProcessedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!current) return;
    if (lastProcessedRef.current === current.regime_label) return;
    lastProcessedRef.current = current.regime_label;
    const next = _computeBannerState(current.regime_label);
    if (next) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: regime is external (polled SWR + localStorage) so we must mirror it into local state to render the banner
      setBanner(next);
    }
  }, [current]);

  if (!banner) return null;

  const dismiss = () => {
    if (typeof window !== "undefined") {
      localStorage.setItem(
        DISMISS_KEY_PREFIX + banner.to,
        String(Date.now()),
      );
    }
    setBanner(null);
  };

  return (
    <div
      className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100 flex items-center justify-between gap-3"
      data-testid="regime-change-banner"
      role="status"
    >
      <span>
        Regime changed: <strong>{banner.from}</strong> →{" "}
        <strong>{banner.to}</strong>. Strategies bound to{" "}
        <strong>{banner.from}</strong> only are now off-regime —
        review applicable strategies.
      </span>
      <button
        type="button"
        onClick={dismiss}
        className="rounded bg-amber-100 px-2 py-0.5 text-amber-900 hover:bg-amber-200 dark:bg-amber-900/50 dark:text-amber-100 dark:hover:bg-amber-900"
        data-testid="regime-change-banner-dismiss"
      >
        Dismiss
      </button>
    </div>
  );
}
