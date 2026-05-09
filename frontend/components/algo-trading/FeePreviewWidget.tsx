// frontend/components/algo-trading/FeePreviewWidget.tsx
"use client";
/**
 * Fee Preview Widget — Slice 1's only UI surface. Sits on the
 * Settings tab. Lets the user enter a hypothetical trade and
 * see an itemised INR fee breakdown, with the rates_version
 * stamp visible so they know which rate ladder applied.
 */

import { useMemo, useState } from "react";

import {
  useFeePreview,
  type FeePreviewParams,
} from "@/hooks/useFeePreview";

const EXCHANGES = ["NSE", "BSE"] as const;
const SIDES = ["BUY", "SELL"] as const;
const PRODUCTS = ["DELIVERY", "INTRADAY"] as const;

export function FeePreviewWidget() {
  const [symbol, setSymbol] = useState("RELIANCE");
  const [exchange, setExchange] = useState<"NSE" | "BSE">("NSE");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [product, setProduct] = useState<"DELIVERY" | "INTRADAY">(
    "DELIVERY",
  );
  const [qty, setQty] = useState(10);
  const [price, setPrice] = useState(2945.2);

  const params: FeePreviewParams | null = useMemo(() => {
    if (!symbol.trim() || qty <= 0 || price <= 0) return null;
    return { symbol: symbol.trim(), exchange, side, product, qty, price };
  }, [symbol, exchange, side, product, qty, price]);

  const { value, loading, error } = useFeePreview(params);

  return (
    <section
      data-testid="algo-fee-preview"
      className="rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40 p-3 space-y-3"
    >
      <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200">
        Fee preview
      </h3>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Calculator-grade fee breakdown using the dated YAML rate
        ladder. Backtest fills will use the same model.
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
        <Input
          label="Symbol" value={symbol} onChange={setSymbol}
          testId="algo-fee-symbol"
        />
        <Select
          label="Exchange" value={exchange}
          options={EXCHANGES}
          onChange={(v) => setExchange(v as "NSE" | "BSE")}
          testId="algo-fee-exchange"
        />
        <Select
          label="Side" value={side}
          options={SIDES}
          onChange={(v) => setSide(v as "BUY" | "SELL")}
          testId="algo-fee-side"
        />
        <Select
          label="Product" value={product}
          options={PRODUCTS}
          onChange={(v) => setProduct(v as "DELIVERY" | "INTRADAY")}
          testId="algo-fee-product"
        />
        <NumInput
          label="Qty" value={qty} onChange={setQty} step="1"
          testId="algo-fee-qty"
        />
        <NumInput
          label="Price (₹)" value={price} onChange={setPrice} step="0.05"
          testId="algo-fee-price"
        />
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-xs p-2"
        >
          {error}
        </div>
      )}

      <div
        data-testid="algo-fee-breakdown"
        className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs"
      >
        <Row label="Brokerage" inr={value?.brokerage_inr} />
        <Row label="STT" inr={value?.stt_inr} />
        <Row label="Exchange txn" inr={value?.exchange_txn_inr} />
        <Row label="SEBI" inr={value?.sebi_inr} />
        <Row label="Stamp duty" inr={value?.stamp_duty_inr} />
        <Row label="GST (18%)" inr={value?.gst_inr} />
        <Row label="DP charges" inr={value?.dp_charges_inr} />
        <Row label="Total" inr={value?.total_inr} bold />
      </div>

      {value && (
        <p className="text-[10px] text-gray-400 dark:text-gray-500">
          Rate ladder: {value.rates_version}
          {loading ? " · refreshing…" : ""}
        </p>
      )}
    </section>
  );
}

function Input({
  label, value, onChange, testId,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  testId: string;
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-gray-500 dark:text-gray-400">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
        className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
      />
    </label>
  );
}

function NumInput({
  label, value, onChange, step, testId,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step: string;
  testId: string;
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-gray-500 dark:text-gray-400">{label}</span>
      <input
        type="number"
        value={value}
        step={step}
        min={0}
        onChange={(e) => onChange(Number(e.target.value))}
        data-testid={testId}
        className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
      />
    </label>
  );
}

function Select<T extends string>({
  label, value, options, onChange, testId,
}: {
  label: string;
  value: T;
  options: readonly T[];
  onChange: (v: T) => void;
  testId: string;
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-gray-500 dark:text-gray-400">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        data-testid={testId}
        className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
      >
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </label>
  );
}

function Row({
  label, inr, bold,
}: { label: string; inr: string | undefined; bold?: boolean }) {
  return (
    <>
      <span
        className={`text-gray-600 dark:text-gray-300 ${bold ? "font-semibold" : ""}`}
      >
        {label}
      </span>
      <span
        className={`text-right tabular-nums ${bold ? "font-semibold" : ""} text-gray-700 dark:text-gray-200`}
      >
        {inr === undefined ? "—" : `₹${inr}`}
      </span>
    </>
  );
}
