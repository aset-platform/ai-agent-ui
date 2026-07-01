"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/apiFetch";

interface Props {
  ticker: string;
  strategyId: string;
  qty: number;
  lastPrice: string | number | null;
}

/** Returns true if NSE is currently open (9:15–15:30 IST, Mon–Fri). */
function isNseOpen(): boolean {
  const now = new Date();
  const ist = new Date(
    now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }),
  );
  const day = ist.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const mins = ist.getHours() * 60 + ist.getMinutes();
  return mins >= 555 && mins <= 930; // 9:15=555, 15:30=930
}

export function UserExitButton({
  ticker,
  strategyId,
  qty,
  lastPrice,
}: Props) {
  const [marketOpen, setMarketOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Recheck market-open state every 30 s without re-rendering the page.
  useEffect(() => {
    setMarketOpen(isNseOpen());
    const id = setInterval(() => setMarketOpen(isNseOpen()), 30_000);
    return () => clearInterval(id);
  }, []);

  if (!marketOpen) return null;

  const priceDisplay =
    lastPrice != null
      ? `₹${Number(lastPrice).toLocaleString("en-IN", {
          maximumFractionDigits: 2,
        })}`
      : "—";

  async function handleConfirm() {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/v1/algo/live/positions/exit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker,
          strategy_id: strategyId,
          qty,
          // UI-displayed price as last-resort fallback for the backend
          // if both kite.ltp() and ws_hwm are unavailable.
          price_hint:
            lastPrice != null && Number.isFinite(Number(lastPrice))
              ? Number(lastPrice)
              : null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          (body as { detail?: string })?.detail ?? `HTTP ${res.status}`,
        );
      }
      setConfirming(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Exit failed");
    } finally {
      setLoading(false);
    }
  }

  if (confirming) {
    return (
      <div
        className="flex flex-col gap-1 rounded border border-rose-300
          bg-rose-50 p-2 text-xs dark:border-rose-700
          dark:bg-rose-950/40"
        data-testid="user-exit-confirm-modal"
      >
        <span className="font-medium text-rose-900 dark:text-rose-200">
          Exit {ticker} × {qty} ≈ {priceDisplay}?
        </span>
        <span className="text-rose-700 dark:text-rose-400">
          Cancels GTT and places a limit sell immediately.
        </span>
        {error && (
          <span className="font-medium text-rose-600 dark:text-rose-300">
            {error}
          </span>
        )}
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={handleConfirm}
            disabled={loading}
            className="rounded bg-rose-600 px-2 py-0.5 text-[11px]
              font-semibold text-white hover:bg-rose-700
              disabled:opacity-50"
            data-testid="user-exit-confirm-btn"
          >
            {loading ? "…" : "Confirm Exit"}
          </button>
          <button
            type="button"
            onClick={() => {
              setConfirming(false);
              setError(null);
            }}
            disabled={loading}
            className="rounded px-2 py-0.5 text-[11px] font-medium
              text-slate-600 hover:text-slate-900
              dark:text-slate-400 dark:hover:text-slate-100"
            data-testid="user-exit-cancel-btn"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => setConfirming(true)}
      className="rounded border border-rose-300 px-2 py-0.5
        text-[11px] font-semibold text-rose-700
        hover:bg-rose-50 dark:border-rose-700 dark:text-rose-400
        dark:hover:bg-rose-950/40"
      data-testid={`exit-position-btn-${ticker}`}
    >
      Exit
    </button>
  );
}
