"use client";

import type { PaperEvent } from "@/hooks/usePaperEvents";

interface Props {
  events: PaperEvent[];
  loading: boolean;
}

const EVENT_TONE: Record<string, string> = {
  signal_generated:
    "border-sky-300 bg-sky-50 dark:border-sky-700 dark:bg-sky-950/30",
  signal_rejected:
    "border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/30",
  order_filled:
    "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/30",
  order_submitted:
    "border-slate-300 bg-slate-50 dark:border-slate-700 dark:bg-slate-900",
  order_cancelled:
    "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30",
  position_opened:
    "border-indigo-300 bg-indigo-50 dark:border-indigo-700 dark:bg-indigo-950/30",
  position_closed:
    "border-indigo-300 bg-indigo-50 dark:border-indigo-700 dark:bg-indigo-950/30",
  risk_breach:
    "border-rose-400 bg-rose-100 dark:border-rose-600 dark:bg-rose-950/50",
};

function fmtTime(ts_ns: number): string {
  return new Date(ts_ns / 1_000_000).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    day: "2-digit",
    month: "short",
  });
}

function summary(event: PaperEvent): string {
  const p = event.payload;
  const ticker = (p["ticker"] as string) ?? "?";
  const side = (p["side"] as string) ?? "";
  const qty = (p["qty"] as number) ?? "";
  switch (event.type) {
    case "signal_generated":
      return `${side} ${qty} ${ticker}`;
    case "signal_rejected": {
      const reason = (p["reason"] as string) ?? "unknown";
      return `${side} ${qty} ${ticker} — rejected: ${reason}`;
    }
    case "order_filled": {
      const price = (p["fill_price"] as string) ?? "?";
      return `${side} ${qty} ${ticker} @ ₹${price}`;
    }
    default:
      return `${ticker}`;
  }
}

export function PaperEventsTimeline({ events, loading }: Props) {
  if (loading) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="paper-events-loading"
      >
        Loading events…
      </div>
    );
  }
  if (events.length === 0) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="paper-events-empty"
      >
        No paper-trading events yet. Start a paper run for an
        active strategy to see signals + fills here.
      </div>
    );
  }

  return (
    <div className="space-y-1.5" data-testid="paper-events-timeline">
      {events.map((e) => {
        const tone =
          EVENT_TONE[e.type]
          ?? "border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900";
        return (
          <div
            key={e.event_id}
            className={`flex items-center justify-between rounded-md border px-3 py-1.5 text-sm ${tone}`}
            data-testid={`paper-event-${e.type}`}
          >
            <span className="font-mono text-xs text-slate-600 dark:text-slate-400">
              {fmtTime(e.ts_ns)}
            </span>
            <span className="flex-1 px-3 text-slate-900 dark:text-slate-100">
              <span className="font-medium">{e.type}</span>
              <span className="ml-2 text-slate-700 dark:text-slate-300">
                {summary(e)}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
