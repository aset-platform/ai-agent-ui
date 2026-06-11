"use client";

import { useState } from "react";
import { useAddToWatchlist } from "@/hooks/useAddToWatchlist";

interface Props {
  disabled: boolean;
  tooltip?: string;
  fetchTickers: () => Promise<string[]>;
  onAdded?: () => void;
}

export function AddFilteredToWatchlistButton(
  { disabled, tooltip, fetchTickers, onAdded }: Props,
) {
  const { submit, submitting } = useAddToWatchlist();
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleClick() {
    setBusy(true);
    setMsg(null);
    try {
      const tickers = await fetchTickers();
      if (tickers.length === 0) {
        setMsg("No tickers match the current filter");
        return;
      }
      const res = await submit(tickers);
      const errs = res.errors.length
        ? ` · ${res.errors.length} error(s)`
        : "";
      setMsg(
        `Added ${res.added.length} · `
        + `${res.skipped_already_linked.length} already in watchlist`
        + errs,
      );
      onAdded?.();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(false);
      window.setTimeout(() => setMsg(null), 6000);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || busy || submitting}
        title={tooltip}
        data-testid="aa-add-to-watchlist"
        className="inline-flex items-center gap-1 rounded-md border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/40 px-2.5 py-1 text-xs font-medium text-indigo-700 dark:text-indigo-300 disabled:opacity-50"
      >
        {busy || submitting ? "Adding…" : "Add to Watchlist"}
      </button>
      {msg && (
        <span
          data-testid="aa-add-to-watchlist-result"
          className="text-[11px] text-slate-500 dark:text-slate-400"
        >
          {msg}
        </span>
      )}
    </div>
  );
}
