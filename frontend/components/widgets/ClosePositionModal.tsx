"use client";

import { useState } from "react";

/** Map ISO currency code to display symbol (mirrors WatchlistWidget). */
function currencySymbol(code: string): string {
  const map: Record<string, string> = {
    USD: "$",
    INR: "₹",
    EUR: "€",
    GBP: "£",
    JPY: "¥",
  };
  return map[code?.toUpperCase()] ?? code ?? "$";
}

export interface ClosePositionHolding {
  ticker: string;
  quantity: number;
  avg_price: number;
  currency: string;
}

export interface ClosePositionBody {
  quantity: number;
  sell_price: number;
  sell_date: string;
  fees: number;
  notes?: string;
}

interface ClosePositionModalProps {
  isOpen: boolean;
  holding: ClosePositionHolding;
  onClose: () => void;
  onConfirm: (body: ClosePositionBody) => Promise<void> | void;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function ClosePositionModal({
  isOpen,
  holding,
  onClose,
  onConfirm,
}: ClosePositionModalProps) {
  const [quantity, setQuantity] = useState(
    String(holding.quantity),
  );
  const [sellPrice, setSellPrice] = useState("");
  const [sellDate, setSellDate] = useState(todayIso());
  const [fees, setFees] = useState("0");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  if (!isOpen) return null;

  const qty = parseFloat(quantity);
  const px = parseFloat(sellPrice);
  const feesNum = parseFloat(fees) || 0;
  const qtyValid =
    !Number.isNaN(qty) && qty > 0 && qty <= holding.quantity;
  const priceValid = !Number.isNaN(px) && px > 0;

  const realized =
    !Number.isNaN(px) && !Number.isNaN(qty)
      ? (px - holding.avg_price) * qty - feesNum
      : 0;
  const sym = currencySymbol(holding.currency);
  const pnlPositive = realized >= 0;

  const handleSubmit = async () => {
    if (!qtyValid) {
      setError(
        `Quantity must be > 0 and <= ${holding.quantity}`,
      );
      return;
    }
    if (!priceValid) {
      setError("Sell price must be > 0");
      return;
    }

    setSaving(true);
    setError("");
    try {
      await onConfirm({
        quantity: qty,
        sell_price: px,
        sell_date: sellDate,
        fees: feesNum,
        notes: notes || undefined,
      });
      onClose();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Failed to close position",
      );
    } finally {
      setSaving(false);
    }
  };

  const confirmDisabled = saving || !qtyValid || !priceValid;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />
      <div
        data-testid="close-position-modal"
        className="relative w-full max-w-sm rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-xl p-6 mx-4"
      >
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-1">
          Close {holding.ticker}
        </h2>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
          Record a sale and preview realized P&amp;L
        </p>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              Quantity
            </label>
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              min="0"
              max={holding.quantity}
              step="1"
              data-testid="close-qty-input"
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              Sell Price
            </label>
            <input
              type="number"
              value={sellPrice}
              onChange={(e) => setSellPrice(e.target.value)}
              min="0"
              step="0.01"
              data-testid="close-price-input"
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              Sell Date
            </label>
            <input
              type="date"
              value={sellDate}
              onChange={(e) => setSellDate(e.target.value)}
              data-testid="close-date-input"
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              Fees
            </label>
            <input
              type="number"
              value={fees}
              onChange={(e) => setFees(e.target.value)}
              min="0"
              step="0.01"
              data-testid="close-fees-input"
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </div>
        </div>

        <div className="mb-4">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
            Notes
          </label>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            data-testid="close-notes-input"
            className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
          />
        </div>

        <div className="mb-4 rounded-lg bg-gray-50 dark:bg-gray-800 px-3 py-2">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Realized P&amp;L
          </p>
          <p
            data-testid="close-pnl-preview"
            className={`text-sm font-semibold ${
              pnlPositive
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400"
            }`}
          >
            {pnlPositive ? "+" : "-"}
            {sym}
            {Math.abs(realized).toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </p>
        </div>

        {error && (
          <p
            data-testid="close-error"
            className="text-xs text-red-600 dark:text-red-400 mb-3"
          >
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={confirmDisabled}
            data-testid="close-confirm"
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          >
            {saving ? "Closing..." : "Close Position"}
          </button>
        </div>
      </div>
    </div>
  );
}
