"use client";
import { useCallback, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface BuildFixtureResult {
  filename: string;
  n_tickers: number;
  n_trigger_dates: number;
  n_ticks: number;
  trigger_tickers: string[];
}

export function useBuildReplayFixture() {
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<BuildFixtureResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (lookbackDays = 180): Promise<BuildFixtureResult> => {
      setSubmitting(true);
      setError(null);
      setResult(null);
      try {
        const r = await apiFetch(
          `${API_URL}/algo/paper/fixtures/build`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lookback_days: lookbackDays }),
          },
        );
        if (!r.ok) {
          const b = await r.text();
          const m = `Build failed: ${r.status} ${b}`;
          setError(m);
          throw new Error(m);
        }
        const data = (await r.json()) as BuildFixtureResult;
        setResult(data);
        return data;
      } finally {
        setSubmitting(false);
      }
    },
    [],
  );

  return { submit, submitting, result, error };
}
