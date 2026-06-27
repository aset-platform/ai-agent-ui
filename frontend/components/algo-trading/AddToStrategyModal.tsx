"use client";
/**
 * AddToStrategyModal — merge a filtered ticker list into a strategy's
 * allowed_tickers caps, with a preview + confirm flow.
 *
 * Flow:
 *  1. Strategy dropdown (useStrategies)
 *  2. After selection: left=existing, right=from filter, Merge button
 *  3. After merge: final deduped list + Save button
 */

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { useStrategies } from "@/hooks/useStrategies";
import { useLiveCaps, upsertLiveCaps } from "@/hooks/useLiveCaps";

interface Props {
  filteredTickers: string[];
  onClose: () => void;
}

// ── Sub-component: strategy caps side-panel ─────────────────────

function StrategyCapsPanel({
  strategyId,
  filteredTickers,
  onClose,
}: {
  strategyId: string;
  filteredTickers: string[];
  onClose: () => void;
}) {
  const { caps, loading: capsLoading } = useLiveCaps(strategyId);
  const existing = useMemo(
    () => caps?.allowed_tickers ?? [],
    [caps],
  );
  const fromFilter = useMemo(
    () =>
      filteredTickers.filter((t) => !existing.includes(t)),
    [filteredTickers, existing],
  );
  const alreadyIn = useMemo(
    () => filteredTickers.filter((t) => existing.includes(t)),
    [filteredTickers, existing],
  );

  const [merged, setMerged] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  // Reset merge state whenever the strategy changes
  useEffect(() => {
    setMerged(null);
    setSaved(false);
    setSaveErr(null);
  }, [strategyId]);

  function handleMerge() {
    const union = Array.from(
      new Set([...existing, ...filteredTickers]),
    ).sort();
    setMerged(union);
    setSaved(false);
    setSaveErr(null);
  }

  async function handleSave() {
    if (!merged || !caps) return;
    setSaving(true);
    setSaveErr(null);
    try {
      await upsertLiveCaps(strategyId, {
        max_inr: Number(caps.max_inr),
        max_orders_per_day: caps.max_orders_per_day,
        allowed_tickers: merged,
        gtt_limit_headroom_pct: Number(caps.gtt_limit_headroom_pct ?? 0.01),
      });
      setSaved(true);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (capsLoading) {
    return (
      <p className="text-xs text-slate-500 animate-pulse mt-4">
        Loading strategy caps…
      </p>
    );
  }

  return (
    <div className="mt-4 space-y-4">
      {/* Two-column: existing vs from filter */}
      {!merged && (
        <>
          <div className="grid grid-cols-2 gap-3">
            {/* Left — already in strategy */}
            <div>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
                Already in strategy ({existing.length})
              </p>
              <div className="rounded border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 max-h-48 overflow-y-auto p-2 space-y-0.5">
                {existing.length === 0 ? (
                  <p className="text-[11px] text-slate-400 italic">
                    None configured
                  </p>
                ) : (
                  existing.map((t) => (
                    <div
                      key={t}
                      className="text-xs font-mono text-slate-700 dark:text-slate-300"
                    >
                      {t}
                      {alreadyIn.includes(t) && (
                        <span className="ml-1.5 text-[9px] font-semibold text-amber-500 uppercase">
                          also in filter
                        </span>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Right — from current filter */}
            <div>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
                From current filter ({filteredTickers.length})
              </p>
              <div className="rounded border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 max-h-48 overflow-y-auto p-2 space-y-0.5">
                {filteredTickers.length === 0 ? (
                  <p className="text-[11px] text-slate-400 italic">
                    No stocks in current filter
                  </p>
                ) : (
                  filteredTickers.map((t) => (
                    <div
                      key={t}
                      className={`text-xs font-mono ${
                        existing.includes(t)
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-emerald-700 dark:text-emerald-400"
                      }`}
                    >
                      {t}
                      {existing.includes(t) && (
                        <span className="ml-1.5 text-[9px] font-semibold text-amber-500 uppercase">
                          dup
                        </span>
                      )}
                    </div>
                  ))
                )}
              </div>
              <p className="mt-1 text-[10px] text-slate-400">
                <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                  +{fromFilter.length} new
                </span>
                {alreadyIn.length > 0 && (
                  <span className="ml-2 text-amber-500 font-medium">
                    {alreadyIn.length} duplicate
                    {alreadyIn.length !== 1 ? "s" : ""}
                  </span>
                )}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={handleMerge}
            disabled={filteredTickers.length === 0}
            className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium
              text-white hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed
              transition-colors"
            data-testid="add-to-strategy-merge-btn"
          >
            Merge →
          </button>
        </>
      )}

      {/* Final merged list + save */}
      {merged && (
        <>
          <div>
            <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
              Final list after merge ({merged.length} tickers)
            </p>
            <div className="rounded border border-indigo-200 dark:border-indigo-700 bg-indigo-50/40 dark:bg-indigo-900/20 max-h-56 overflow-y-auto p-2">
              <div className="flex flex-wrap gap-1.5">
                {merged.map((t) => (
                  <span
                    key={t}
                    className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-mono font-medium ${
                      !existing.includes(t)
                        ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                        : "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300"
                    }`}
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
            <p className="mt-1 text-[10px] text-slate-400">
              Green = newly added. Review before saving.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setMerged(null)}
              className="rounded border border-slate-300 dark:border-slate-600 px-3 py-1.5
                text-xs text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              ← Back
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || saved}
              className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium
                text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors"
              data-testid="add-to-strategy-save-btn"
            >
              {saving ? "Saving…" : saved ? "Saved ✓" : "Save to strategy"}
            </button>
            {saved && (
              <button
                type="button"
                onClick={onClose}
                className="text-xs text-indigo-600 dark:text-indigo-400 underline"
              >
                Close
              </button>
            )}
          </div>

          {saveErr && (
            <p className="text-xs text-rose-600" data-testid="add-to-strategy-save-err">
              {saveErr}
            </p>
          )}
        </>
      )}
    </div>
  );
}

// ── Main modal ───────────────────────────────────────────────────

export function AddToStrategyModal({ filteredTickers, onClose }: Props) {
  const { strategies: allStrategies, loading: stLoading } = useStrategies();
  const strategies = allStrategies.filter(
    (s) => s.mode === "live" && !s.archived_at,
  );
  const [selectedId, setSelectedId] = useState<string>("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) setMounted(true); });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  if (!mounted) return null;

  const modal = (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Add tickers to strategy"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        className="relative z-10 w-full max-w-2xl rounded-xl bg-white dark:bg-slate-900
          shadow-2xl border border-slate-200 dark:border-slate-700
          p-6 max-h-[90vh] overflow-y-auto"
        data-testid="add-to-strategy-modal"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              Add to strategy allowed tickers
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              {filteredTickers.length} ticker
              {filteredTickers.length !== 1 ? "s" : ""} from current filter
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200
              hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            aria-label="Close"
            data-testid="add-to-strategy-close"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Strategy select */}
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
            Select strategy
          </span>
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="rounded border border-slate-300 dark:border-slate-600 px-2 py-1.5
              text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100
              focus:outline-none focus:ring-2 focus:ring-indigo-500"
            data-testid="add-to-strategy-select"
          >
            <option value="">
              {stLoading
                ? "Loading strategies…"
                : strategies.length === 0
                  ? "No live strategies"
                  : "— pick a live strategy —"}
            </option>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>

        {/* Side panels + actions */}
        {selectedId && (
          <StrategyCapsPanel
            key={selectedId}
            strategyId={selectedId}
            filteredTickers={filteredTickers}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
