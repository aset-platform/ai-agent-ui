"use client";

import { useEffect, useRef, useState } from "react";
import { useBuildReplayFixture } from "@/hooks/useBuildReplayFixture";

interface Props {
  onBulkAdd: () => void;
  onRemoveAll: () => void;
}

export function WatchlistOverflowMenu(
  { onBulkAdd, onRemoveAll }: Props,
) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { submit, submitting, result, error } = useBuildReplayFixture();

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    function onDocClick(e: MouseEvent) {
      if (cancelled) return;
      if (
        ref.current
        && !ref.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="dashboard-watchlist-overflow-button"
        className="p-1 rounded-md text-gray-400 hover:text-indigo-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        aria-label="More watchlist actions"
        title="More actions"
      >
        <svg
          className="w-4 h-4"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="5" r="1" />
          <circle cx="12" cy="12" r="1" />
          <circle cx="12" cy="19" r="1" />
        </svg>
      </button>
      {open && (
        <div
          className="absolute right-0 mt-1 w-44 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow z-[60] text-xs"
          data-testid="dashboard-watchlist-overflow-menu"
          role="menu"
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onBulkAdd();
            }}
            data-testid="dashboard-watchlist-bulk-add-item"
            className="block w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            Bulk add tickers…
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onRemoveAll();
            }}
            data-testid="dashboard-watchlist-remove-all-item"
            className="block w-full text-left px-3 py-2 text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/30"
          >
            Remove all…
          </button>
          <button
            type="button"
            disabled={submitting}
            onClick={() => {
              setOpen(false);
              submit();
            }}
            data-testid="watchlist-build-fixture"
            className="block w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? "Building…" : "Build RSI(2) replay fixture"}
          </button>
        </div>
      )}
      {(result || error) && (
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400 max-w-[11rem] break-words">
          {error
            ? error
            : result && result.n_trigger_dates === 0
              ? "No oversold (RSI2≤5) setups in the lookback window"
              : result
                ? `Built ${result.filename} · ${result.n_tickers} tickers · ${result.n_trigger_dates} trigger dates`
                : null}
        </p>
      )}
    </div>
  );
}
