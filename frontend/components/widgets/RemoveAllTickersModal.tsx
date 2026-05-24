"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { UnlinkAllResponse } from "@/lib/types/bulkTickers";

interface Props {
  currentCount: number;
  onClose: () => void;
  onRemoved: () => void; // parent SWR mutate()
}

export function RemoveAllTickersModal(
  { currentCount, onClose, onRemoved }: Props,
) {
  const [val, setVal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const enabled = val === "REMOVE ALL";

  async function handleConfirm() {
    if (!enabled) return;
    setSubmitting(true);
    setErr(null);
    try {
      const r = await apiFetch(
        `${API_URL}/users/me/tickers/all`,
        {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: "REMOVE ALL" }),
        },
      );
      if (!r.ok) {
        const body = await r.text();
        setErr(`Remove failed: ${r.status} ${body}`);
        return;
      }
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const _data = (await r.json()) as UnlinkAllResponse;
      onRemoved();
      onClose();
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? `Remove failed: ${exc.message}`
          : "Remove failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="remove-all-tickers-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-96 space-y-3">
        <h3 className="text-sm font-semibold">
          Remove all tickers from watchlist
        </h3>
        <p className="text-xs text-slate-500">
          This will remove all {currentCount.toLocaleString("en-IN")}{" "}
          ticker{currentCount === 1 ? "" : "s"} from your
          watchlist. Holdings (Portfolio) and algo positions
          are NOT affected.
        </p>
        <label className="flex flex-col gap-1 text-xs">
          <span>Type &quot;REMOVE ALL&quot; to confirm:</span>
          <input
            type="text"
            value={val}
            onChange={(e) => setVal(e.target.value)}
            data-testid="remove-all-tickers-input"
            className="rounded border border-slate-300 dark:border-slate-600 px-2 py-1 font-mono"
            placeholder="REMOVE ALL"
          />
        </label>
        {err && (
          <p className="text-xs text-rose-600">{err}</p>
        )}
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded border px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!enabled || submitting}
            data-testid="remove-all-tickers-confirm-button"
            className="rounded bg-rose-600 text-white px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {submitting
              ? "Removing…"
              : `Remove all ${currentCount.toLocaleString("en-IN")}`}
          </button>
        </div>
      </div>
    </div>
  );
}
