"use client";

import { useCallback, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { BulkTickerResponse } from "@/lib/types/bulkTickers";

export function useAddToWatchlist() {
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<BulkTickerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  const submit = useCallback(
    async (tickers: string[]): Promise<BulkTickerResponse> => {
      setSubmitting(true);
      setError(null);
      try {
        const r = await apiFetch(
          `${API_URL}/users/me/tickers/bulk-add`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tickers }),
          },
        );
        if (!r.ok) {
          const body = await r.text();
          const msg = `Add failed: ${r.status} ${body}`;
          setError(msg);
          throw new Error(msg);
        }
        const data = (await r.json()) as BulkTickerResponse;
        setResult(data);
        return data;
      } finally {
        setSubmitting(false);
      }
    },
    [],
  );

  return { submit, submitting, result, error, reset };
}
