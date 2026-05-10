"use client";

/**
 * LiveWsHealthDot — OBS-1 Kite WS health traffic light.
 *
 * Compact 8px coloured dot for the Live segment header, fed by
 * the ``useWsHealth`` SWR hook. Hover/touch reveals a textual
 * snapshot (tick count today, age of last tick, subscriber and
 * subscribed-token counts).
 *
 * Status colour ladder:
 *   - disconnected (slate)  — no Kite WS connection
 *   - no-tick     (slate)   — connected but never received a tick
 *   - green       (emerald) — last tick ≤ 30 s
 *   - amber       (amber)   — last tick 31–120 s
 *   - red         (rose)    — last tick > 120 s
 *
 * Pure-DOM ``title`` attribute is used for the tooltip so the
 * component stays SSR-safe and Lighthouse-friendly — no JS
 * tooltip library, no portal, no dark-mode toggle wiring.
 */

import { useWsHealth, type WsHealth } from "@/hooks/useWsHealth";

export type WsHealthStatus =
  | "disconnected"
  | "no-tick"
  | "green"
  | "amber"
  | "red";

/** Pure function for unit-testable status calculation. */
export function statusFromAge(
  ageSeconds: number | null,
  connected: boolean,
): WsHealthStatus {
  if (!connected) return "disconnected";
  if (ageSeconds === null || ageSeconds === undefined) {
    return "no-tick";
  }
  if (ageSeconds <= 30) return "green";
  if (ageSeconds <= 120) return "amber";
  return "red";
}

const STATUS_CLASS: Record<WsHealthStatus, string> = {
  disconnected: "bg-slate-400 dark:bg-slate-500",
  "no-tick": "bg-slate-400 dark:bg-slate-500",
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-rose-500",
};

const STATUS_LABEL: Record<WsHealthStatus, string> = {
  disconnected: "disconnected",
  "no-tick": "connected — no ticks yet",
  green: "connected — live ticks",
  amber: "connected — stale",
  red: "connected — very stale",
};

function formatAge(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "n/a";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function buildTooltip(
  status: WsHealthStatus,
  health: WsHealth | null,
): string {
  if (health === null) {
    return "Kite WS — loading…";
  }
  const label = STATUS_LABEL[status];
  const lines = [
    `Kite WS: ${label}`,
    `Subscribers: ${health.subscriber_count} `
      + `(tokens: ${health.subscribed_tokens})`,
    `Last tick: ${formatAge(health.tick_age_seconds)}`,
    `Ticks today: ${health.tick_count_today}`,
  ];
  return lines.join(" · ");
}

interface Props {
  /** Optional override (lets parents share a snapshot or feed a
   *  test fixture). When omitted, the component drives itself
   *  via ``useWsHealth``. */
  health?: WsHealth | null;
}

export function LiveWsHealthDot({ health: override }: Props) {
  // Hook is always called (Rules of Hooks); when an override is
  // supplied we ignore the hook's data so callers can test pure
  // status semantics without SWR.
  const { health: fetched } = useWsHealth();
  const health = override !== undefined ? override : fetched;

  const connected = health?.connected ?? false;
  const ageSeconds = health?.tick_age_seconds ?? null;
  const status = statusFromAge(ageSeconds, connected);
  const colourClass = STATUS_CLASS[status];
  const tooltip = buildTooltip(status, health ?? null);

  return (
    <span
      role="status"
      aria-label={tooltip}
      title={tooltip}
      data-testid="live-ws-health-dot"
      data-status={status}
      className={
        "inline-block h-2 w-2 rounded-full ring-1 "
        + "ring-black/10 dark:ring-white/15 "
        + colourClass
      }
    />
  );
}
