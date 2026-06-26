"use client";
/**
 * CleanupStrategyModal — review and trim a strategy's allowed_tickers.
 *
 * Chips are colour-coded:
 *   green  = ticker is in the user's live portfolio holdings
 *   amber  = ticker appears in the current watchlist filter
 *   red    = ticker is in neither (candidate for removal)
 *
 * Clicking the × on a chip stages a removal; Save commits the trimmed
 * list via PUT /v1/algo/live/caps/{strategy_id}.
 */

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { usePortfolio } from "@/hooks/usePortfolio";
import { useLiveHoldings } from "@/hooks/useLiveHoldings";
import { useLivePositions } from "@/hooks/useLivePositions";
import { useStrategies } from "@/hooks/useStrategies";
import { useLiveCaps, upsertLiveCaps } from "@/hooks/useLiveCaps";

interface Props {
  /** Tickers currently visible in the watchlist filter. */
  filteredTickers: string[];
  onClose: () => void;
}

// ── Chip ─────────────────────────────────────────────────────────

function TickerChip({
  ticker,
  color,
  onRemove,
}: {
  ticker: string;
  color: "green" | "amber" | "red";
  onRemove: () => void;
}) {
  const cls =
    color === "green"
      ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300 border border-green-200 dark:border-green-800"
      : color === "amber"
        ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300 border border-amber-200 dark:border-amber-800"
        : "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300 border border-red-200 dark:border-red-800";

  return (
    <span
      className={`relative inline-flex items-center gap-1 rounded-full pl-2.5 pr-1 py-0.5 text-[11px] font-mono font-medium ${cls}`}
    >
      {ticker}
      <button
        type="button"
        onClick={onRemove}
        className="ml-0.5 flex items-center justify-center w-4 h-4 rounded-full
          hover:bg-black/10 dark:hover:bg-white/15 transition-colors"
        aria-label={`Remove ${ticker}`}
      >
        <svg
          className="w-2.5 h-2.5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
        >
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </button>
    </span>
  );
}

// ── Caps panel (loaded after strategy selection) ──────────────────

function CapsCleanupPanel({
  strategyId,
  filteredSet,
  holdingSet,
  onClose,
}: {
  strategyId: string;
  filteredSet: Set<string>;
  holdingSet: Set<string>;
  onClose: () => void;
}) {
  const { caps, loading: capsLoading } = useLiveCaps(strategyId);

  const [tickers, setTickers] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const colorPriority = (t: string) =>
    holdingSet.has(t) ? 0 : filteredSet.has(t) ? 1 : 2;

  const sorted = useMemo(
    () =>
      tickers
        ? [...tickers].sort(
            (a, b) =>
              colorPriority(a) - colorPriority(b) || a.localeCompare(b),
          )
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tickers, holdingSet, filteredSet],
  );

  useEffect(() => {
    if (caps) {
      setTickers([...caps.allowed_tickers]);
      setSaved(false);
      setSaveErr(null);
    }
  }, [caps]);

  function chipColor(t: string): "green" | "amber" | "red" {
    if (holdingSet.has(t)) return "green";
    if (filteredSet.has(t)) return "amber";
    return "red";
  }

  function remove(t: string) {
    setTickers((prev) => (prev ? prev.filter((x) => x !== t) : prev));
    setSaved(false);
  }

  async function handleSave() {
    if (!tickers || !caps) return;
    setSaving(true);
    setSaveErr(null);
    try {
      await upsertLiveCaps(strategyId, {
        max_inr: Number(caps.max_inr),
        max_orders_per_day: caps.max_orders_per_day,
        allowed_tickers: tickers,
        gtt_limit_headroom_pct: Number(caps.gtt_limit_headroom_pct ?? 0.01),
      });
      setSaved(true);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (capsLoading || tickers === null || sorted === null) {
    return (
      <p className="mt-4 text-xs text-slate-500 animate-pulse">
        Loading allowed tickers…
      </p>
    );
  }

  const original = caps?.allowed_tickers.length ?? 0;
  const removed = original - tickers.length;

  return (
    <div className="mt-4 space-y-4">
      {/* Legend */}
      <div className="flex flex-wrap items-center gap-4 text-[11px] text-slate-600 dark:text-slate-400">
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-green-400 shrink-0" />
          In holdings
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-amber-400 shrink-0" />
          In current filter
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-red-400 shrink-0" />
          Not in filter or holdings
        </span>
        <span className="ml-auto text-slate-400">
          {tickers.length} / {original} remaining
        </span>
      </div>

      {/* Chip grid */}
      <div
        className="rounded-lg border border-slate-200 dark:border-slate-700
          bg-slate-50 dark:bg-slate-800/50 p-3 min-h-[4rem] max-h-80 overflow-y-auto"
        data-testid="cleanup-strategy-chips"
      >
        {sorted.length === 0 ? (
          <p className="text-xs text-slate-400 italic">
            All tickers removed — save to apply.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {sorted.map((t) => (
              <TickerChip
                key={t}
                ticker={t}
                color={chipColor(t)}
                onRemove={() => remove(t)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer actions */}
      <div className="flex items-center gap-3">
        {removed > 0 && (
          <span className="text-xs text-slate-500">
            {removed} ticker{removed !== 1 ? "s" : ""} staged for removal
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {saved && (
            <button
              type="button"
              onClick={onClose}
              className="text-xs text-indigo-600 dark:text-indigo-400 underline"
            >
              Close
            </button>
          )}
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || saved || removed === 0}
            data-testid="cleanup-strategy-save-btn"
            className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white
              hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed
              transition-colors"
          >
            {saving ? "Saving…" : saved ? "Saved ✓" : "Save"}
          </button>
        </div>
      </div>

      {saveErr && (
        <p
          className="text-xs text-rose-600"
          data-testid="cleanup-strategy-save-err"
        >
          {saveErr}
        </p>
      )}
    </div>
  );
}

// ── Main modal ────────────────────────────────────────────────────

const EXCHANGE_SUFFIX: Record<string, string> = {
  NSE: ".NS",
  BSE: ".BO",
};

export function CleanupStrategyModal({ filteredTickers, onClose }: Props) {
  const { strategies: all, loading: stLoading } = useStrategies();
  const { holdings } = usePortfolio();
  const { rows: liveRows } = useLiveHoldings();
  const { rows: posRows } = useLivePositions();

  const strategies = useMemo(
    () =>
      all.filter(
        (s) =>
          (s.mode === "paper" || s.mode === "live") && !s.archived_at,
      ),
    [all],
  );

  const holdingSet = useMemo(() => {
    const tickers = new Set<string>();
    // Platform portfolio (non-Kite)
    for (const h of holdings) {
      if (h.quantity > 0) tickers.add(h.ticker);
    }
    // Kite settled holdings (T+2 done + T+1 pending)
    for (const row of liveRows ?? []) {
      if (row.quantity > 0 || row.t1_pending) {
        const suffix = EXCHANGE_SUFFIX[row.exchange] ?? `.${row.exchange}`;
        tickers.add(`${row.tradingsymbol}${suffix}`);
      }
    }
    // Algo positions tracker — catches CNC buys not yet T+2 settled
    for (const row of posRows ?? []) {
      if (row.quantity > 0) {
        const suffix = EXCHANGE_SUFFIX[row.exchange] ?? `.${row.exchange}`;
        tickers.add(`${row.tradingsymbol}${suffix}`);
      }
    }
    return tickers;
  }, [holdings, liveRows, posRows]);

  const filteredSet = useMemo(
    () => new Set(filteredTickers),
    [filteredTickers],
  );

  const [selectedId, setSelectedId] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!mounted) return null;

  const modal = (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Cleanup strategy allowed tickers"
    >
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        className="relative z-10 w-full max-w-2xl rounded-xl bg-white dark:bg-slate-900
          shadow-2xl border border-slate-200 dark:border-slate-700
          p-6 max-h-[90vh] overflow-y-auto"
        data-testid="cleanup-strategy-modal"
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              Cleanup strategy allowed tickers
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Remove tickers from a strategy's allowed list. Red chips are not
              in your holdings or current filter.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200
              hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            aria-label="Close"
            data-testid="cleanup-strategy-close"
          >
            <svg
              className="w-4 h-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Strategy picker */}
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
            Select strategy (paper or live)
          </span>
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            data-testid="cleanup-strategy-select"
            className="rounded border border-slate-300 dark:border-slate-600 px-2 py-1.5
              text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100
              focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">
              {stLoading
                ? "Loading strategies…"
                : strategies.length === 0
                  ? "No active / live strategies"
                  : "— pick a strategy —"}
            </option>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} [{s.mode}]
              </option>
            ))}
          </select>
        </label>

        {selectedId && (
          <CapsCleanupPanel
            key={selectedId}
            strategyId={selectedId}
            filteredSet={filteredSet}
            holdingSet={holdingSet}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
