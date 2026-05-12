"use client";

/**
 * LiveEventsPanel — compact, scrollable feed of recent live-mode
 * algo.events for the user. Mounts beside LiveActiveRunsPanel on
 * the Live page so testers can watch signals + order outcomes
 * land in real time without leaving the page.
 *
 * Surfaces every event LiveRuntime emits (signal_generated,
 * signal_rejected, order_submitted_live, order_rejected_live,
 * order_filled_live, order_cancelled_live, order_cancelled_timeout,
 * order_duplicate_blocked, order_ltp_stale_blocked,
 * order_freeze_chunked, freeze_qty_fallback_applied,
 * position_hydrated) with a colour-coded badge + the most
 * salient payload fields. Click a row to expand its raw payload.
 *
 * Reuses usePaperEvents (mode="live", dry_run=false) — same SWR
 * key as RecentFillsTape so the two stay synchronised.
 */

import { useMemo, useState } from "react";

import { usePaperEvents } from "@/hooks/usePaperEvents";

interface BadgeStyle {
  bg: string;
  text: string;
  label: string;
}

// Type → badge styling. Anything not in the map falls back to slate.
const TYPE_BADGE: Record<string, BadgeStyle> = {
  signal_generated: {
    bg: "bg-blue-100 dark:bg-blue-900/40",
    text: "text-blue-800 dark:text-blue-200",
    label: "SIGNAL",
  },
  signal_rejected: {
    bg: "bg-amber-100 dark:bg-amber-900/40",
    text: "text-amber-800 dark:text-amber-200",
    label: "REJ",
  },
  order_submitted_live: {
    bg: "bg-indigo-100 dark:bg-indigo-900/40",
    text: "text-indigo-800 dark:text-indigo-200",
    label: "SUBMIT",
  },
  order_filled_live: {
    bg: "bg-emerald-100 dark:bg-emerald-900/40",
    text: "text-emerald-800 dark:text-emerald-200",
    label: "FILL",
  },
  order_rejected_live: {
    bg: "bg-rose-100 dark:bg-rose-900/40",
    text: "text-rose-800 dark:text-rose-200",
    label: "REJ",
  },
  order_cancelled_live: {
    bg: "bg-slate-200 dark:bg-slate-700",
    text: "text-slate-800 dark:text-slate-200",
    label: "CXL",
  },
  order_cancelled_timeout: {
    bg: "bg-slate-200 dark:bg-slate-700",
    text: "text-slate-800 dark:text-slate-200",
    label: "CXL-TTL",
  },
  order_cancel_failed: {
    bg: "bg-rose-100 dark:bg-rose-900/40",
    text: "text-rose-800 dark:text-rose-200",
    label: "CXL-FAIL",
  },
  order_duplicate_blocked: {
    bg: "bg-amber-100 dark:bg-amber-900/40",
    text: "text-amber-800 dark:text-amber-200",
    label: "DUP",
  },
  order_ltp_stale_blocked: {
    bg: "bg-amber-100 dark:bg-amber-900/40",
    text: "text-amber-800 dark:text-amber-200",
    label: "STALE",
  },
  order_freeze_chunked: {
    bg: "bg-indigo-100 dark:bg-indigo-900/40",
    text: "text-indigo-800 dark:text-indigo-200",
    label: "CHUNK",
  },
  freeze_qty_fallback_applied: {
    bg: "bg-slate-200 dark:bg-slate-700",
    text: "text-slate-700 dark:text-slate-300",
    label: "FRZ-DEF",
  },
  position_hydrated: {
    bg: "bg-purple-100 dark:bg-purple-900/40",
    text: "text-purple-800 dark:text-purple-200",
    label: "HYDRATE",
  },
};

const FALLBACK_BADGE: BadgeStyle = {
  bg: "bg-slate-200 dark:bg-slate-700",
  text: "text-slate-700 dark:text-slate-300",
  label: "EVT",
};

function badgeFor(type: string): BadgeStyle {
  return TYPE_BADGE[type] ?? {
    ...FALLBACK_BADGE,
    label: type.slice(0, 8).toUpperCase(),
  };
}

// Render-IST time-of-day. Date is implicit from "today's events".
function fmtTimeIst(tsNs: number): string {
  try {
    const ms = Math.floor(tsNs / 1_000_000);
    return new Date(ms).toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "—";
  }
}

// One-line summary extracted from the payload — keeps the row
// scannable. Type-specific so we surface the most useful field.
function summarise(
  type: string,
  payload: Record<string, unknown>,
): string {
  const sym = String(
    payload["symbol"] ?? payload["ticker"] ?? "",
  );
  const side = String(payload["side"] ?? "");
  const qty = payload["qty"] ?? payload["quantity"];
  const price = (
    payload["price"]
    ?? payload["limit_price"]
    ?? payload["avg_price"]
    ?? payload["last_price"]
  );
  const reason = String(payload["reason"] ?? "");

  switch (type) {
    case "signal_generated":
      return `${sym} ${side}${qty != null ? ` × ${qty}` : ""}`
        + (reason ? ` — ${reason}` : "");
    case "signal_rejected":
      return `${sym} ${side}${qty != null ? ` × ${qty}` : ""}`
        + ` — ${reason || "rejected"}`;
    case "order_submitted_live":
      return `${sym} ${side} × ${qty}`
        + (price != null ? ` @ ₹${price}` : "");
    case "order_filled_live":
      return `${sym} ${side} × ${qty}`
        + (price != null ? ` @ ₹${price}` : "");
    case "order_rejected_live":
      return `${sym} ${side} × ${qty} — ${reason || "rejected"}`;
    case "order_ltp_stale_blocked":
      return `${sym} blocked — LTP ${payload["age_seconds"]}s old`;
    case "order_duplicate_blocked":
      return `${sym} ${side} × ${qty} — duplicate within 60s`;
    case "order_cancelled_timeout": {
      const age = payload["age_seconds"];
      return `${sym} cancelled${age != null ? ` after ${age}s` : ""}`;
    }
    case "order_freeze_chunked":
      return `${sym} ${qty} split into `
        + `${payload["chunk_total"] ?? "?"} chunks`;
    case "freeze_qty_fallback_applied":
      return `${sym} freeze qty defaulted`;
    case "position_hydrated":
      return `${sym} × ${qty}${
        payload["t1_pending"] ? " (T+1)" : ""
      } — ${payload["source"]}`;
    default:
      return sym || "—";
  }
}

export function LiveEventsPanel() {
  // Pull the last 100 live, real-money events for this user.
  // 5s SWR refresh matches usePaperEvents default. Filter out
  // dry_run rows defensively (the Live page is decoupled from
  // dry-run but a fresh deploy might still hold legacy rows).
  const { events, loading, error } = usePaperEvents(
    100, 0, "live", false,
  );

  // Newest at the top; payload-collapsed by default.
  const sorted = useMemo(
    () => [...events].sort((a, b) => b.ts_ns - a.ts_ns),
    [events],
  );

  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div
      className="rounded-md border border-slate-200
        dark:border-slate-700 p-3 flex flex-col h-full"
      data-testid="live-events-panel"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold
          text-slate-900 dark:text-slate-100">
          Events
        </h3>
        <span className="text-[10px] uppercase tracking-wide
          font-medium text-slate-500">
          Live · real money
        </span>
      </div>

      {error && (
        <p
          className="mt-2 text-xs text-rose-600"
          data-testid="live-events-error"
        >
          Could not load events: {error}
        </p>
      )}

      {/* Scrollable feed with explicit max-height so the panel
          stays compact regardless of the row mate's height —
          ~5-7 rows visible, scroll for the rest. Relying on
          h-full alone left the panel expanding to fit all events
          on pages where the grid row was unconstrained. */}
      <div
        className="mt-2 flex-1 min-h-0 max-h-[260px]
          overflow-y-auto"
        data-testid="live-events-feed"
      >
        {loading && sorted.length === 0 && (
          <p className="text-xs text-slate-500">Loading…</p>
        )}
        {!loading && sorted.length === 0 && (
          <p
            className="text-xs text-slate-500"
            data-testid="live-events-empty"
          >
            No events yet. Start the runtime and signals will land
            here as they fire.
          </p>
        )}
        <ul className="space-y-1">
          {sorted.map((e) => {
            const bg = badgeFor(e.type);
            const isOpen = expanded === e.event_id;
            return (
              <li
                key={e.event_id}
                className="rounded border border-slate-100
                  dark:border-slate-800
                  hover:border-slate-200
                  dark:hover:border-slate-700"
                data-testid={`live-event-${e.event_id}`}
              >
                <button
                  type="button"
                  onClick={() =>
                    setExpanded(isOpen ? null : e.event_id)
                  }
                  className="w-full text-left px-2 py-1.5
                    flex items-center gap-2 text-[11px]"
                >
                  <span
                    className="font-mono text-slate-500
                      shrink-0 tabular-nums"
                    title={`ts_ns=${e.ts_ns}`}
                  >
                    {fmtTimeIst(e.ts_ns)}
                  </span>
                  <span
                    className={`inline-flex items-center
                      shrink-0 rounded px-1.5 py-0.5
                      text-[9px] font-semibold uppercase
                      tracking-wide ${bg.bg} ${bg.text}`}
                  >
                    {bg.label}
                  </span>
                  <span className="truncate
                    text-slate-700 dark:text-slate-300">
                    {summarise(e.type, e.payload)}
                  </span>
                </button>
                {isOpen && (
                  <pre className="px-2 pb-2 text-[10px]
                    leading-tight text-slate-600
                    dark:text-slate-400
                    overflow-x-auto whitespace-pre-wrap break-all"
                  >
                    {JSON.stringify(e.payload, null, 2)}
                  </pre>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
