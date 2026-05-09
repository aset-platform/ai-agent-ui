"use client";

import type { PaperEvent } from "@/hooks/usePaperEvents";

export const EVENTS_PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;

export type EventsPageSize =
  (typeof EVENTS_PAGE_SIZE_OPTIONS)[number];

interface Props {
  events: PaperEvent[];
  loading: boolean;
  /** 0-indexed current page (matches Insights tabs convention). */
  page?: number;
  pageSize?: EventsPageSize;
  total?: number;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: EventsPageSize) => void;
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

export function PaperEventsTimeline({
  events,
  loading,
  page = 0,
  pageSize = 100,
  total = 0,
  onPageChange,
  onPageSizeChange,
}: Props) {
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
  if (events.length === 0 && page === 0) {
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

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(0, page), totalPages - 1);
  const showControls = onPageChange != null;

  return (
    <div className="space-y-2" data-testid="paper-events-timeline">
      <div className="space-y-1.5">
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

      {showControls && total > 0 && (
        <div
          className="flex flex-wrap items-center justify-between gap-3 pt-2 text-xs text-gray-600 dark:text-gray-400"
          data-testid="paper-events-pager"
        >
          <div className="flex items-center gap-2">
            <span>{total.toLocaleString()} events</span>
            {onPageSizeChange != null && (
              <select
                value={pageSize}
                onChange={(e) => {
                  onPageSizeChange(
                    Number(e.target.value) as EventsPageSize,
                  );
                  onPageChange(0);
                }}
                className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
                data-testid="paper-events-page-size"
              >
                {EVENTS_PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}/page
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={safePage <= 0}
              onClick={() =>
                onPageChange(Math.max(0, safePage - 1))
              }
              className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              data-testid="paper-events-prev"
            >
              Prev
            </button>
            <span className="tabular-nums">
              {safePage + 1} / {totalPages}
            </span>
            <button
              type="button"
              disabled={safePage >= totalPages - 1}
              onClick={() =>
                onPageChange(
                  Math.min(totalPages - 1, safePage + 1),
                )
              }
              className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              data-testid="paper-events-next"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
