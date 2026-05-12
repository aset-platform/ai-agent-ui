"use client";

import { usePaperEvents } from "@/hooks/usePaperEvents";

/**
 * Footer-zone tape: latest live fills (real money + dry-run live).
 *
 * Backend event type is `order_filled_live` (not `order_filled` —
 * that's paper) and the payload keys are `symbol` / `side` / `qty`
 * / `price` (see backend/algo/live/runtime.py). Timestamp is
 * nanoseconds since epoch on `ts_ns`.
 *
 * Filters by ``type=order_filled_live`` server-side so high-volume
 * non-fill events (signal_generated, signal_rejected) can't push
 * the actual fills out of the limit-20 window — the previous
 * client-side filter was silently dropping morning fills after
 * the runtime's mid-day signal storm.
 */
export function RecentFillsTape() {
  const { events: fills } = usePaperEvents(
    20, 0, "live", null, "order_filled_live",
  );
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-3"
      data-testid="recent-fills-tape"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Recent Fills
      </h3>
      {fills.length === 0 && (
        <p className="mt-2 text-xs text-slate-400">
          No fills yet today.
        </p>
      )}
      <ul className="mt-2 max-h-48 overflow-y-auto space-y-1 text-xs font-mono">
        {fills.map((e) => {
          const p = e.payload;
          const tsMs = Math.floor(Number(e.ts_ns) / 1_000_000);
          const time = Number.isFinite(tsMs)
            ? new Date(tsMs).toLocaleTimeString("en-IN")
            : "—";
          return (
            <li
              key={e.event_id}
              className="text-slate-700 dark:text-slate-300"
            >
              {time} · {String(p.side ?? "")} {String(p.qty ?? "")}{" "}
              {String(p.symbol ?? "")} @ ₹{String(p.price ?? "")}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
