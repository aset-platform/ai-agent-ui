"use client";

import { useMemo } from "react";

import { formatIstTime, todayIstIso } from "@/lib/datetime";
import { usePaperEvents } from "@/hooks/usePaperEvents";

/**
 * Footer-zone tape: latest LIVE fills today (real money only).
 *
 * Pulls three event types and merges them:
 *   - ``order_filled_live`` — exchange fills placed by the runtime
 *   - ``gtt_triggered`` — GTT exits detected by the 15-min ratchet
 *     poll or the postback fallback (both paths emit this type so
 *     GTT exits always appear here even without a matching in-flight
 *     entry)
 *   - ``user_exit_initiated`` — user-triggered exits (LIMIT SELL
 *     submitted; shows intent immediately before fill arrives)
 *
 * Scoped to: mode=live, dry_run=false, today IST.
 */
export function RecentFillsTape() {
  const today = todayIstIso();
  const { events: normalFills } = usePaperEvents(
    20, 0, "live", false, "order_filled_live", today,
  );
  const { events: gttFills } = usePaperEvents(
    20, 0, "live", false, "gtt_triggered", today,
  );
  const { events: userExitFills } = usePaperEvents(
    20, 0, "live", false, "user_exit_initiated", today,
  );

  const fills = useMemo(() => {
    return [...normalFills, ...gttFills, ...userExitFills]
      .sort((a, b) => b.ts_ns - a.ts_ns)
      .slice(0, 20);
  }, [normalFills, gttFills, userExitFills]);

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
          const time = formatIstTime(tsMs);
          // Normalise across order_filled_live (symbol/price) and
          // gtt_triggered (ticker/stop_price) payload shapes.
          const sym = String(p.symbol ?? p.ticker ?? "");
          const isGtt = e.type === "gtt_triggered";
          const isUserExit = e.type === "user_exit_initiated";
          const side =
            isGtt || isUserExit ? "SELL" : String(p.side ?? "");
          const price = String(
            p.price ?? p.stop_price ?? "",
          );
          return (
            <li
              key={e.event_id}
              className="text-slate-700 dark:text-slate-300"
            >
              {time} · {side} {String(p.qty ?? "")}{" "}
              {sym} @ ₹{price}
              {isGtt && (
                <span
                  className="ml-1 text-[9px] text-amber-600
                    dark:text-amber-400 font-semibold uppercase"
                >
                  GTT
                </span>
              )}
              {isUserExit && (
                <span
                  className="ml-1 text-[9px] text-rose-600
                    dark:text-rose-400 font-semibold uppercase"
                >
                  USR
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
