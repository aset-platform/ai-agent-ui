"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { SweepResult } from "@/lib/types/algoSweep";

interface Props {
  run: SweepResult;
  onClose: () => void;
}

export function SweepPromoteModal(
  { run, onClose }: Props,
) {
  const winnerIdx = run.winner_variant_index;
  const winner = winnerIdx !== null
    ? run.variants[winnerIdx] : null;
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (!winner) {
    return null;
  }

  async function handleConfirm() {
    setSubmitting(true);
    setErr(null);
    try {
      const r = await apiFetch(
        `${API_URL}/algo/strategies/`
        + `${run.base_strategy_id}/clone`,
        { method: "POST" },
      );
      if (!r.ok) {
        throw new Error(
          `Clone failed: ${r.status}`,
        );
      }
      const body = await r.json();
      setDone(body?.id ?? body?.strategy_id ?? "new");
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? exc.message
          : "Failed to clone strategy",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="sweep-promote-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-96 space-y-3">
        <h3 className="text-sm font-semibold">
          Save winner as new strategy
        </h3>
        {done ? (
          <>
            <p className="text-xs text-emerald-700 dark:text-emerald-300">
              ✅ Strategy cloned successfully.
            </p>
            <p className="text-xs">
              Next step: open the Strategies tab and edit
              the new clone&apos;s{" "}
              <code className="text-[11px] bg-slate-100 dark:bg-slate-800 px-1 rounded">
                {run.swept_field}
              </code>{" "}
              field to{" "}
              <code className="text-[11px] bg-slate-100 dark:bg-slate-800 px-1 rounded">
                {String(winner.swept_value)}
              </code>{" "}
              (the sweep winner). v1 doesn&apos;t patch the
              AST automatically.
            </p>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm"
              >
                Close
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs">
              Winning value: {run.swept_field}=
              {String(winner.swept_value)} (Sharpe=
              {winner.sharpe}).
            </p>
            <p className="text-xs text-slate-500">
              This will clone the base strategy. After
              cloning, edit the new strategy&apos;s{" "}
              <code className="text-[11px] bg-slate-100 dark:bg-slate-800 px-1 rounded">
                {run.swept_field}
              </code>{" "}
              to{" "}
              <code className="text-[11px] bg-slate-100 dark:bg-slate-800 px-1 rounded">
                {String(winner.swept_value)}
              </code>{" "}
              via the Strategies tab.
            </p>
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
                disabled={submitting}
                className="rounded bg-emerald-600 text-white px-3 py-1.5 text-sm"
              >
                {submitting ? "Cloning…" : "Clone strategy"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
