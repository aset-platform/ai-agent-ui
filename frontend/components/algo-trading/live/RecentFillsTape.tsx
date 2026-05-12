"use client";

import { usePaperEvents } from "@/hooks/usePaperEvents";

/** Today's date in IST as YYYY-MM-DD — used to bound the fills
 *  query so we don't bleed prior sessions into the panel. */
function todayIstIso(): string {
  // en-CA produces ISO-style YYYY-MM-DD; explicit IST timezone
  // keeps the boundary at midnight IST regardless of viewer
  // locale (feedback_ist_dates_user_facing).
  return new Date().toLocaleDateString("en-CA", {
    timeZone: "Asia/Kolkata",
  });
}

/**
 * Footer-zone tape: latest LIVE fills today (real money only).
 *
 * Backend event type is `order_filled_live` (not `order_filled` —
 * that's paper) and the payload keys are `symbol` / `side` / `qty`
 * / `price` (see backend/algo/live/runtime.py). Timestamp is
 * nanoseconds since epoch on `ts_ns`.
 *
 * Server-side filters keep the panel scoped to what the trader
 * actually wants to see on the Live page:
 *   - ``type=order_filled_live`` — only real fills
 *   - ``mode=live`` — never paper / backtest
 *   - ``dry_run=false`` — never synthetic rehearsal fills
 *   - ``since_date=<today IST>`` — never yesterday's session
 * Previously the panel pulled every type+mode+day mixed and
 * filtered client-side, which leaked dry-run fills + prior
 * sessions into the visible list.
 */
export function RecentFillsTape() {
  const { events: fills } = usePaperEvents(
    20, 0, "live", false, "order_filled_live", todayIstIso(),
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
