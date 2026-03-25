"use client";

import { useState } from "react";

interface EditStockModalProps {
  isOpen: boolean;
  ticker: string;
  currentQty: number;
  currentPrice: number;
  onClose: () => void;
  onSave: (data: {
    quantity?: number;
    price?: number;
    trade_date?: string;
  }) => Promise<void>;
}

export function EditStockModal({
  isOpen,
  ticker,
  currentQty,
  currentPrice,
  onClose,
  onSave,
}: EditStockModalProps) {
  const [quantity, setQuantity] = useState(
    String(currentQty),
  );
  const [price, setPrice] = useState(
    String(currentPrice),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  if (!isOpen) return null;

  const handleSubmit = async () => {
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

    setSaving(true);
    setError("");
    try {
      await onSave({ quantity: qty, price: px });
      onClose();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Failed to update",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />
      <div className="relative w-full max-w-sm rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-xl p-6 mx-4">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-1">
          Edit {ticker}
        </h2>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
          Update quantity or buy price
        </p>

        <div className="grid grid-cols-2 gap-3 mb-4">
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
              data-testid="edit-stock-quantity"
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
              data-testid="edit-stock-price"
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </div>
        </div>

        {error && (
          <p
            data-testid="edit-stock-error"
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
            disabled={saving}
            data-testid="edit-stock-save"
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
