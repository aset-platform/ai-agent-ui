"use client";

import { useCallback } from "react";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface PortfolioHolding {
  ticker: string;
  transaction_id: string;
  quantity: number;
  avg_price: number;
  current_price: number | null;
  currency: string;
  market: string;
  invested: number;
  current_value: number | null;
  gain_loss_pct: number | null;
}

export interface PortfolioResponse {
  holdings: PortfolioHolding[];
  totals: Record<string, number>;
}

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function usePortfolio() {
  const { data, error, isLoading, mutate } =
    useSWR<PortfolioResponse>(
      `${API_URL}/users/me/portfolio`,
      fetcher,
      {
        revalidateOnFocus: false,
        dedupingInterval: 120_000,
      },
    );

  const addHolding = useCallback(
    async (body: {
      ticker: string;
      quantity: number;
      price: number;
      trade_date: string;
      notes?: string;
    }) => {
      const r = await apiFetch(
        `${API_URL}/users/me/portfolio`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
        },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      mutate();
      return r.json();
    },
    [mutate],
  );

  const editHolding = useCallback(
    async (
      transactionId: string,
      body: {
        quantity?: number;
        price?: number;
        trade_date?: string;
      },
    ) => {
      const r = await apiFetch(
        `${API_URL}/users/me/portfolio/${transactionId}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
        },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      mutate();
    },
    [mutate],
  );

  const deleteHolding = useCallback(
    async (transactionId: string) => {
      const r = await apiFetch(
        `${API_URL}/users/me/portfolio/${transactionId}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      mutate();
    },
    [mutate],
  );

  return {
    holdings: data?.holdings ?? [],
    totals: data?.totals ?? {},
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load"
      : null,
    refresh: () => mutate(),
    addHolding,
    editHolding,
    deleteHolding,
  };
}
