"use client";

import { useState } from "react";

import {
  useReplayEvents,
  type ReplayEvent,
} from "@/hooks/useReplayEvents";

const MODES = ["all", "backtest", "paper"] as const;
const TYPES = [
  "all",
  "signal_generated",
  "signal_rejected",
  "order_filled",
  "order_cancelled",
  "risk_breach",
  "backtest_run_started",
  "backtest_run_completed",
] as const;

const TONE: Record<string, string> = {
  signal_generated:
    "border-sky-300 bg-sky-50 dark:border-sky-700 dark:bg-sky-950/30",
  signal_rejected:
    "border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/30",
  order_filled:
    "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/30",
  order_cancelled:
    "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30",
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

function summary(e: ReplayEvent): string {
  const p = e.payload;
  const ticker = (p["ticker"] as string) ?? "";
  const side = (p["side"] as string) ?? "";
  const qty = (p["qty"] as number) ?? "";
  switch (e.type) {
    case "signal_rejected": {
      const reason = (p["reason"] as string) ?? "?";
      return `${side} ${qty} ${ticker} — ${reason}`;
    }
    case "order_filled": {
      const price = (p["fill_price"] as string) ?? "?";
      return `${side} ${qty} ${ticker} @ ₹${price}`;
    }
    case "signal_generated":
      return `${side} ${qty} ${ticker}`;
    default:
      return ticker;
  }
}

export function ReplayTab() {
  const [mode, setMode] = useState<string>("all");
  const [type, setType] = useState<string>("all");

  const filters = {
    mode: mode === "all" ? undefined : mode,
    type: type === "all" ? undefined : type,
    limit: 200,
  };
  const { events, loading, error } = useReplayEvents(filters);

  return (
    <div className="space-y-4" data-testid="replay-tab">
      <div>
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Replay
        </h2>
        <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
          Cross-mode event-log timeline. Per spec § 9.1 slice 10.
        </p>
      </div>

      <div
        className="flex flex-wrap items-center gap-3"
        data-testid="replay-filters"
      >
        <Field label="Mode">
          <select
            className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            data-testid="replay-mode-select"
          >
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Type">
          <select
            className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
            value={type}
            onChange={(e) => setType(e.target.value)}
            data-testid="replay-type-select"
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>
        <span className="text-xs text-slate-500">
          {events.length} event{events.length === 1 ? "" : "s"}
        </span>
      </div>

      {error && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="replay-error"
        >
          {error}
        </div>
      )}

      {!loading && events.length === 0 && !error && (
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
          data-testid="replay-empty"
        >
          No events match the current filters.
        </div>
      )}

      {events.length > 0 && (
        <div
          className="space-y-1.5"
          data-testid="replay-timeline"
        >
          {events.map((e) => {
            const tone =
              TONE[e.type]
              ?? "border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900";
            return (
              <div
                key={e.event_id}
                className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${tone}`}
                data-testid={`replay-event-${e.type}`}
              >
                <span className="font-mono text-xs text-slate-600 dark:text-slate-400">
                  {fmtTime(e.ts_ns)}
                </span>
                <span className="rounded bg-white/60 px-1.5 py-0.5 text-xs font-medium text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                  {e.mode}
                </span>
                <span className="flex-1 text-slate-900 dark:text-slate-100">
                  <span className="font-medium">{e.type}</span>
                  <span className="ml-2 text-slate-700 dark:text-slate-300">
                    {summary(e)}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-600 dark:text-slate-400">
      <span>{label}</span>
      {children}
    </label>
  );
}
