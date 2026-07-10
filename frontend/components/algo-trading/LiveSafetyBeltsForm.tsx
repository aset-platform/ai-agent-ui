"use client";

import { useEffect, useRef, useState } from "react";

import { upsertLiveCaps, useLiveCaps } from "@/hooks/useLiveCaps";
import { useLiveStatus } from "@/hooks/useLiveStatus";

interface Props {
  strategyId: string;
}

// ── Ticker chip ───────────────────────────────────────────────────

export function TickerChip({
  ticker,
  offUniverse,
  onRemove,
}: {
  ticker: string;
  offUniverse: boolean;
  onRemove: () => void;
}) {
  return (
    <span
      className={
        offUniverse
          ? "inline-flex items-center gap-1 rounded-full border " +
            "border-red-300 bg-red-50 pl-2.5 pr-1 py-0.5 text-[11px] " +
            "font-mono font-medium text-red-700 dark:border-red-700 " +
            "dark:bg-red-900/20 dark:text-red-400"
          : "inline-flex items-center gap-1 rounded-full border " +
            "border-indigo-200 bg-indigo-100 pl-2.5 pr-1 py-0.5 " +
            "text-[11px] font-mono font-medium text-indigo-800 " +
            "dark:border-indigo-700 dark:bg-indigo-900/40 " +
            "dark:text-indigo-300"
      }
      title={
        offUniverse
          ? "Not in the liquidity-screened trading universe " +
            "(ADTV/market-cap floor, or an ETF with no fundamentals " +
            "row). Bar-derived features can never populate for this " +
            "ticker — signals on it will always be rejected."
          : undefined
      }
      data-testid={`live-caps-ticker-${ticker}`}
    >
      {offUniverse ? "⚠ " : ""}
      {ticker}
      <button
        type="button"
        onClick={onRemove}
        className="ml-0.5 flex h-4 w-4 items-center justify-center
          rounded-full hover:bg-black/10 dark:hover:bg-white/15
          transition-colors"
        aria-label={`Remove ${ticker}`}
        data-testid={`live-caps-ticker-remove-${ticker}`}
      >
        <svg
          className="h-2.5 w-2.5"
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

// ── Tag input (chips + inline add) ───────────────────────────────

function TickerTagInput({
  value,
  onChange,
  offUniverseTickers,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  offUniverseTickers: string[];
}) {
  const [input, setInput] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function commit(raw: string) {
    const parts = raw
      .split(",")
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    const next = [...value];
    for (const t of parts) {
      if (!next.includes(t)) next.push(t);
    }
    onChange(next);
    setInput("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (input.trim()) commit(input);
    } else if (e.key === "Backspace" && !input && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData("text");
    if (text.includes(",")) {
      e.preventDefault();
      commit(text);
    }
  }

  return (
    <div
      ref={containerRef}
      className="flex flex-wrap gap-1.5 rounded border border-slate-300
        bg-white p-2 dark:border-slate-600 dark:bg-slate-800
        cursor-text min-h-[40px]"
      onClick={() => inputRef.current?.focus()}
      data-testid="live-caps-allowed-tickers"
    >
      {value.map((t) => (
        <TickerChip
          key={t}
          ticker={t}
          offUniverse={offUniverseTickers.includes(t)}
          onRemove={() => onChange(value.filter((x) => x !== t))}
        />
      ))}
      <input
        ref={inputRef}
        value={input}
        onChange={(e) => setInput(e.target.value.toUpperCase())}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        onBlur={() => {
          if (input.trim()) commit(input);
        }}
        placeholder={
          value.length === 0 ? "Type NSE symbol + Enter to add…" : ""
        }
        className="min-w-[160px] flex-1 bg-transparent text-sm
          outline-none text-slate-800 dark:text-slate-100
          placeholder:text-slate-400"
        data-testid="live-caps-ticker-input"
      />
    </div>
  );
}

// ── Form ──────────────────────────────────────────────────────────

export function LiveSafetyBeltsForm({ strategyId }: Props) {
  const { caps, loading } = useLiveCaps(strategyId);
  const { revalidate: revalidateStatus } = useLiveStatus(strategyId);

  const [maxInr, setMaxInr] = useState<string>("");
  const [maxOrders, setMaxOrders] = useState<string>("");
  const [tickerList, setTickerList] = useState<string[]>([]);
  const [gttHeadroomPct, setGttHeadroomPct] = useState<string>("1.0");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (caps) {
      setMaxInr(String(caps.max_inr ?? 0));
      setMaxOrders(String(caps.max_orders_per_day ?? 0));
      setTickerList(caps.allowed_tickers ?? []);
      setGttHeadroomPct(
        String(((caps.gtt_limit_headroom_pct ?? 0.01) * 100).toFixed(2)),
      );
    }
  }, [caps]);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    setErr(null);
    try {
      await upsertLiveCaps(strategyId, {
        max_inr: Number(maxInr),
        max_orders_per_day: Math.min(50, Math.max(0, Number(maxOrders))),
        allowed_tickers: tickerList,
        gtt_limit_headroom_pct: Math.min(
          0.1,
          Math.max(0, Number(gttHeadroomPct) / 100),
        ),
      });
      setSaved(true);
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
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
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

        {/* GTT limit buffer */}
        <label className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
            GTT limit buffer %
          </span>
          <input
            type="number"
            min={0}
            max={10}
            step={0.1}
            value={gttHeadroomPct}
            onChange={(e) => setGttHeadroomPct(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-sm
              dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
            data-testid="live-caps-gtt-headroom"
          />
          <span className="text-[10px] text-slate-400">
            Limit = trigger × (1 − buffer). Max 10%.
          </span>
        </label>

        {/* Currently committed (read-only) */}
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
            Currently committed (read-only)
          </span>
          <div
            className="rounded border border-slate-200 px-2 py-1 text-sm
              dark:border-slate-700 dark:text-slate-300"
          >
            ₹{caps?.cumulative_inr_today?.toLocaleString("en-IN") ?? 0}
            {" · "}
            {caps?.orders_count_today ?? 0} open
          </div>
          <span className="text-[10px] text-slate-400">
            Σ qty × avg of open positions + holdings via this strategy.
            Returns to 0 on full square-off.
          </span>
        </div>
      </div>

      {/* Allowed tickers — chip tag input */}
      <div className="flex flex-col gap-0.5">
        <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
          Allowed tickers
          <span className="ml-1 font-normal text-slate-400">
            ({tickerList.length} NSE symbols)
          </span>
        </span>
        <TickerTagInput
          value={tickerList}
          onChange={setTickerList}
          offUniverseTickers={caps?.off_universe_tickers ?? []}
        />
        <span className="text-[10px] text-slate-400">
          Type symbol + Enter (or comma) to add · × to remove ·
          paste comma-separated to bulk-add. Empty = all signals
          rejected.
        </span>
      </div>

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
