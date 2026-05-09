"use client";
/**
 * LiveSafetyBeltsForm — V2-5.
 *
 * Form for setting per-strategy live trading caps:
 *   • max daily ₹ notional (max_inr)
 *   • max orders per day
 *   • allowed tickers (comma-separated list)
 *
 * Caps can be changed at any time; they take effect on the NEXT
 * bar evaluation (not retroactively).  The form does NOT touch
 * live_orders_enabled — that is controlled by LiveModeToggle.
 */

import { useEffect, useState } from "react";

import { upsertLiveCaps, useLiveCaps } from "@/hooks/useLiveCaps";
import { useLiveStatus } from "@/hooks/useLiveStatus";

interface Props {
  strategyId: string;
}

export function LiveSafetyBeltsForm({ strategyId }: Props) {
  const { caps, loading } = useLiveCaps(strategyId);
  const { revalidate: revalidateStatus } = useLiveStatus(strategyId);

  const [maxInr, setMaxInr] = useState<string>("");
  const [maxOrders, setMaxOrders] = useState<string>("");
  const [tickers, setTickers] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (caps) {
      setMaxInr(String(caps.max_inr ?? 0));
      setMaxOrders(String(caps.max_orders_per_day ?? 0));
      setTickers((caps.allowed_tickers ?? []).join(", "));
    }
  }, [caps]);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    setErr(null);
    try {
      const tickerList = tickers
        .split(",")
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean);
      await upsertLiveCaps(strategyId, {
        max_inr: Number(maxInr),
        max_orders_per_day: Math.min(50, Math.max(0, Number(maxOrders))),
        allowed_tickers: tickerList,
      });
      setSaved(true);
      // Invalidate gate-status so the toggle re-evaluates caps_set
      await revalidateStatus();
    } catch (exc) {
      setErr(
        exc instanceof Error ? exc.message : "Failed to save caps",
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="live-safety-belts-loading"
      >
        Loading caps…
      </p>
    );
  }

  return (
    <div
      className="space-y-3"
      data-testid="live-safety-belts-form"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {/* Max INR per day */}
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
            Max ₹ per day
          </span>
          <input
            type="number"
            min={0}
            step={1000}
            value={maxInr}
            onChange={(e) => setMaxInr(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm
              dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
            data-testid="live-caps-max-inr"
          />
          <span className="text-[10px] text-slate-400">
            0 = unlimited (not recommended)
          </span>
        </label>

        {/* Max orders per day */}
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
            Max orders / day
          </span>
          <input
            type="number"
            min={0}
            max={50}
            step={1}
            value={maxOrders}
            onChange={(e) => setMaxOrders(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm
              dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
            data-testid="live-caps-max-orders"
          />
          <span className="text-[10px] text-slate-400">
            Max 50; 0 = no new orders
          </span>
        </label>

        {/* Today counters (read-only) */}
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
            Today's usage (read-only)
          </span>
          <div className="rounded border border-slate-200 px-2 py-1 text-sm
            dark:border-slate-700 dark:text-slate-300">
            ₹{caps?.cumulative_inr_today?.toLocaleString("en-IN") ?? 0}
            {" · "}
            {caps?.orders_count_today ?? 0} orders
          </div>
          <span className="text-[10px] text-slate-400">
            Resets at 09:00 IST (Mon–Fri)
          </span>
        </div>
      </div>

      {/* Allowed tickers */}
      <label className="flex flex-col gap-0.5">
        <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
          Allowed tickers (comma-separated, NSE symbols)
        </span>
        <input
          type="text"
          value={tickers}
          onChange={(e) => setTickers(e.target.value)}
          placeholder="RELIANCE, TCS, INFY"
          className="rounded border border-slate-300 px-2 py-1 text-sm
            dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
          data-testid="live-caps-allowed-tickers"
        />
        <span className="text-[10px] text-slate-400">
          Empty list = all signals rejected. At least one ticker is
          required before enabling live trading.
        </span>
      </label>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium
            text-white hover:bg-indigo-700 disabled:opacity-50"
          data-testid="live-caps-save-btn"
        >
          {saving ? "Saving…" : "Save caps"}
        </button>
        {saved && (
          <span
            className="text-xs text-emerald-600 dark:text-emerald-400"
            data-testid="live-caps-saved-indicator"
          >
            Saved
          </span>
        )}
      </div>

      {err && (
        <p
          className="text-xs text-rose-600"
          data-testid="live-caps-save-error"
        >
          {err}
        </p>
      )}
    </div>
  );
}
