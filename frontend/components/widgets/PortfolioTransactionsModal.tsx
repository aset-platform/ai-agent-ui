"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  PortfolioTransaction,
  PortfolioTransactionsResponse,
} from "@/lib/types";
import { EditStockModal } from "./EditStockModal";

interface PortfolioTransactionsModalProps {
  isOpen: boolean;
  ticker: string | null;
  onClose: () => void;
}

const CURRENCY_SYMBOL: Record<string, string> = {
  USD: "$",
  INR: "₹",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
};

function fmtCurrency(
  v: number | null | undefined,
  sym: string,
): string {
  if (v == null) return "—";
  return `${sym}${v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function PortfolioTransactionsModal({
  isOpen,
  ticker,
  onClose,
}: PortfolioTransactionsModalProps) {
  const [data, setData] =
    useState<PortfolioTransactionsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editingTxn, setEditingTxn] =
    useState<PortfolioTransaction | null>(null);

  const fetchData = useCallback(async () => {
    if (!ticker) return;
    setLoading(true);
    setError("");
    try {
      const r = await apiFetch(
        `${API_URL}/users/me/portfolio/${encodeURIComponent(
          ticker,
        )}/transactions`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Failed to load transactions",
      );
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => {
    if (isOpen && ticker) {
      fetchData();
    } else {
      setData(null);
      setError("");
      setEditingTxn(null);
    }
  }, [isOpen, ticker, fetchData]);

  if (!isOpen || !ticker) return null;

  const sym = CURRENCY_SYMBOL[data?.currency ?? ""] ?? "$";
  const summary = data?.summary;
  const positive =
    summary?.gain != null && summary.gain >= 0;

  return (
    <>
      <div
        className="fixed inset-0 z-[70] flex items-center justify-center"
        data-testid="portfolio-transactions-modal"
      >
        <div
          className="absolute inset-0 bg-black/50"
          onClick={onClose}
        />
        <div className="relative w-full max-w-2xl rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-xl mx-4 max-h-[85vh] flex flex-col">
          {/* Header */}
          <div className="flex items-start justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                {ticker} Transactions
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                All purchases and sales for this holding
              </p>
            </div>
            <button
              onClick={onClose}
              data-testid="portfolio-transactions-close"
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1"
              aria-label="Close"
            >
              <svg
                className="w-5 h-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-6 py-4">
            {loading && (
              <div className="py-8 text-center">
                <div className="animate-spin h-6 w-6 border-2 border-indigo-500 border-t-transparent rounded-full mx-auto" />
              </div>
            )}
            {error && (
              <p
                data-testid="portfolio-transactions-error"
                className="text-sm text-red-600 dark:text-red-400 py-4"
              >
                {error}
              </p>
            )}
            {data && data.transactions.length > 0 && (
              <table className="w-full text-sm">
                <thead className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  <tr className="border-b border-gray-100 dark:border-gray-800">
                    <th className="text-left py-2 font-medium">
                      Date
                    </th>
                    <th className="text-left py-2 font-medium">
                      Side
                    </th>
                    <th className="text-right py-2 font-medium">
                      Qty
                    </th>
                    <th className="text-right py-2 font-medium">
                      Price
                    </th>
                    <th className="text-right py-2 font-medium">
                      Amount
                    </th>
                    <th className="w-8"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {data.transactions.map((t) => {
                    const amt = t.quantity * t.price;
                    return (
                      <tr
                        key={t.transaction_id}
                        data-testid={`portfolio-txn-row-${t.transaction_id}`}
                        className="text-gray-900 dark:text-gray-100"
                      >
                        <td className="py-2 text-gray-700 dark:text-gray-300">
                          {t.trade_date}
                        </td>
                        <td className="py-2">
                          <span
                            className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${
                              t.side.toUpperCase() ===
                              "SELL"
                                ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                                : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                            }`}
                          >
                            {t.side.toUpperCase()}
                          </span>
                        </td>
                        <td className="py-2 text-right font-mono">
                          {t.quantity}
                        </td>
                        <td className="py-2 text-right font-mono">
                          {fmtCurrency(t.price, sym)}
                        </td>
                        <td className="py-2 text-right font-mono">
                          {fmtCurrency(amt, sym)}
                        </td>
                        <td className="py-2 text-right">
                          <button
                            onClick={() =>
                              setEditingTxn(t)
                            }
                            data-testid={`portfolio-txn-edit-${t.transaction_id}`}
                            title="Edit transaction"
                            className="p-1 rounded text-gray-400 hover:text-indigo-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                          >
                            <svg
                              className="w-3.5 h-3.5"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                            </svg>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* Summary footer */}
          {summary && (
            <div
              className="border-t border-gray-200 dark:border-gray-700 px-6 py-4 bg-gray-50 dark:bg-gray-800/50 rounded-b-xl"
              data-testid="portfolio-transactions-summary"
            >
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                <div>
                  <p className="text-gray-500 dark:text-gray-400">
                    Total Qty
                  </p>
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 font-mono mt-0.5">
                    {summary.total_quantity}
                  </p>
                </div>
                <div>
                  <p className="text-gray-500 dark:text-gray-400">
                    Avg Price
                  </p>
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 font-mono mt-0.5">
                    {fmtCurrency(summary.avg_price, sym)}
                  </p>
                </div>
                <div>
                  <p className="text-gray-500 dark:text-gray-400">
                    Current Price
                  </p>
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 font-mono mt-0.5">
                    {fmtCurrency(
                      summary.current_price,
                      sym,
                    )}
                  </p>
                </div>
                <div>
                  <p className="text-gray-500 dark:text-gray-400">
                    Gain / Loss
                  </p>
                  <p
                    className={`text-sm font-semibold font-mono mt-0.5 ${
                      summary.gain == null
                        ? "text-gray-500"
                        : positive
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-red-600 dark:text-red-400"
                    }`}
                  >
                    {summary.gain == null
                      ? "—"
                      : `${positive ? "+" : ""}${fmtCurrency(
                          summary.gain,
                          sym,
                        )} (${positive ? "+" : ""}${
                          summary.gain_pct?.toFixed(2) ??
                          "0.00"
                        }%)`}
                  </p>
                </div>
              </div>
              <div className="mt-3 flex justify-between text-xs text-gray-500 dark:text-gray-400">
                <span>
                  Invested:{" "}
                  <span className="font-mono text-gray-700 dark:text-gray-300">
                    {fmtCurrency(summary.invested, sym)}
                  </span>
                </span>
                <span>
                  Current Value:{" "}
                  <span className="font-mono text-gray-700 dark:text-gray-300">
                    {fmtCurrency(
                      summary.current_value,
                      sym,
                    )}
                  </span>
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Per-transaction edit (nested) */}
      {editingTxn && (
        <EditStockModal
          isOpen={editingTxn !== null}
          ticker={ticker}
          currentQty={editingTxn.quantity}
          currentPrice={editingTxn.price}
          onClose={() => setEditingTxn(null)}
          onSave={async (payload) => {
            const r = await apiFetch(
              `${API_URL}/users/me/portfolio/${editingTxn.transaction_id}`,
              {
                method: "PUT",
                headers: {
                  "Content-Type": "application/json",
                },
                body: JSON.stringify(payload),
              },
            );
            if (!r.ok) {
              throw new Error(`HTTP ${r.status}`);
            }
            await fetchData();
          }}
        />
      )}
    </>
  );
}
