"use client";

import { useState, useMemo, useEffect } from "react";

interface AddStockModalProps {
  isOpen: boolean;
  tickers: string[];
  onClose: () => void;
  onAdd: (data: {
    ticker: string;
    quantity: number;
    price: number;
    trade_date: string;
    notes?: string;
  }) => Promise<void>;
  initialTicker?: string;
}

export function AddStockModal({
  isOpen,
  tickers,
  onClose,
  onAdd,
  initialTicker,
}: AddStockModalProps) {
  const [ticker, setTicker] = useState("");
  const [search, setSearch] = useState("");
  const [showDropdown, setShowDropdown] =
    useState(false);

  // Pre-fill when opened from a recommendation
  useEffect(() => {
    if (isOpen && initialTicker) {
      setTicker(initialTicker);
      setSearch(initialTicker);
    }
  }, [isOpen, initialTicker]);
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [tradeDate, setTradeDate] = useState(
    () => new Date().toISOString().slice(0, 10),
  );
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return tickers.slice(0, 20);
    const q = search.trim().toUpperCase();
    return tickers
      .filter((t) => t.includes(q))
      .slice(0, 20);
  }, [tickers, search]);

  const reset = () => {
    setTicker("");
    setSearch("");
    setQuantity("");
    setPrice("");
    setTradeDate(
      new Date().toISOString().slice(0, 10),
    );
    setNotes("");
    setError("");
  };

  const handleSubmit = async () => {
    if (!ticker) {
      setError("Select a ticker");
      return;
    }
    const qty = parseFloat(quantity);
    const px = parseFloat(price);
    if (!qty || qty <= 0) {
      setError("Quantity must be > 0");
      return;
    }
    if (!px || px <= 0) {
      setError("Price must be > 0");
      return;
    }
    if (!tradeDate) {
      setError("Select a buy date");
      return;
    }

    setSaving(true);
    setError("");
    try {
      await onAdd({
        ticker,
        quantity: qty,
        price: px,
        trade_date: tradeDate,
        notes: notes || undefined,
      });
      reset();
      onClose();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Failed to add",
      );
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={() => {
          reset();
          onClose();
        }}
      />

      {/* Modal */}
      <div data-testid="add-stock-modal" className="relative w-full max-w-md rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-xl p-6 mx-4">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
          Add Stock to Portfolio
        </h2>

        {/* Ticker search */}
        <div className="mb-3">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
            Ticker
          </label>
          <div className="relative">
            <input
              type="text"
              value={
                showDropdown ? search : ticker || search
              }
              onChange={(e) => {
                setSearch(
                  e.target.value.toUpperCase(),
                );
                setShowDropdown(true);
                if (ticker) setTicker("");
              }}
              onFocus={() => setShowDropdown(true)}
              onBlur={() =>
                setTimeout(
                  () => setShowDropdown(false),
                  200,
                )
              }
              placeholder="Search ticker..."
              data-testid="add-stock-ticker"
              className="w-full text-sm font-mono rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
            {showDropdown && filtered.length > 0 && (
              <div className="absolute left-0 right-0 top-full mt-1 z-10 max-h-48 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg">
                {filtered.map((t) => (
                  <button
                    key={t}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setTicker(t);
                      setSearch(t);
                      setShowDropdown(false);
                    }}
                    className={`w-full text-left px-3 py-1.5 text-sm font-mono hover:bg-gray-50 dark:hover:bg-gray-800 ${
                      t === ticker
                        ? "text-indigo-600 font-semibold"
                        : "text-gray-700 dark:text-gray-300"
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Quantity + Price */}
        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              Quantity
            </label>
            <input
              type="number"
              value={quantity}
              onChange={(e) =>
                setQuantity(e.target.value)
              }
              min="0"
              step="1"
              placeholder="10"
              data-testid="add-stock-quantity"
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              Buy Price
            </label>
            <input
              type="number"
              value={price}
              onChange={(e) =>
                setPrice(e.target.value)
              }
              min="0"
              step="0.01"
              placeholder="252.82"
              data-testid="add-stock-price"
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </div>
        </div>

        {/* Buy Date */}
        <div className="mb-3">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
            Buy Date
          </label>
          <input
            type="date"
            value={tradeDate}
            onChange={(e) =>
              setTradeDate(e.target.value)
            }
            max={new Date()
              .toISOString()
              .slice(0, 10)}
            data-testid="add-stock-date"
            className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
          />
        </div>

        {/* Notes */}
        <div className="mb-4">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
            Notes (optional)
          </label>
          <input
            type="text"
            value={notes}
            onChange={(e) =>
              setNotes(e.target.value)
            }
            placeholder="e.g., Long-term hold"
            data-testid="add-stock-notes"
            className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
          />
        </div>

        {/* Error */}
        {error && (
          <p
            data-testid="add-stock-error"
            className="text-xs text-red-600 dark:text-red-400 mb-3"
          >
            {error}
          </p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <button
            onClick={() => {
              reset();
              onClose();
            }}
            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            data-testid="add-stock-submit"
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          >
            {saving ? "Adding..." : "Add to Portfolio"}
          </button>
        </div>
      </div>
    </div>
  );
}
