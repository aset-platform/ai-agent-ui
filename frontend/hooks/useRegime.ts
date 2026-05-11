"use client";
/**
 * Hooks for the regime classifier (REGIME-1).
 *
 * - `useRegimeCurrent`: latest BULL / SIDEWAYS / BEAR row + HMM
 *   stress probability, polled every 60s.
 * - `useRegimeHistory`: last N trading days (default 252) of
 *   regime + stress for the history ribbon chart.
 * - `useClassifierHealth`: HMM training freshness for an admin
 *   widget.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface CurrentRegime {
  bar_date: string;
  regime_label: "BULL" | "SIDEWAYS" | "BEAR";
  stress_prob: number | null;
  rule_inputs: Record<string, number | boolean>;
  classifier_version: string;
}

export interface RegimeHistoryRow {
  bar_date: string;
  regime_label: "BULL" | "SIDEWAYS" | "BEAR";
  stress_prob: number | null;
}

export interface ClassifierHealth {
  hmm_trained_through: string | null;
  hmm_age_days: number | null;
  last_regime_bar_date: string | null;
  last_regime_age_days: number | null;
}

async function fetcher<T>(url: string): Promise<T> {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function useRegimeCurrent() {
  const { data, error, isLoading, mutate } = useSWR<CurrentRegime>(
    `${API_URL}/algo/regime/current`,
    fetcher,
    {
      refreshInterval: 60_000,
      revalidateOnFocus: false,
      dedupingInterval: 60_000,
    },
  );
  return {
    current: data ?? null,
    error: error instanceof Error ? error : undefined,
    loading: isLoading,
    revalidate: mutate,
  };
}

export function useRegimeHistory(days = 252) {
  const { data, error, isLoading } = useSWR<{
    rows: RegimeHistoryRow[];
  }>(
    `${API_URL}/algo/regime/history?days=${days}`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 5 * 60_000 },
  );
  return {
    rows: data?.rows ?? [],
    error: error instanceof Error ? error : undefined,
    loading: isLoading,
  };
}

export interface PeriodSummary {
  period_start: string;
  period_end: string;
  total_days: number;
  counts: Record<string, number>;
  pct: Record<string, number>;
  dominant: string | null;
  recommended_template: string | null;
  avg_stress_prob: number | null;
}

export function useRegimePeriodSummary(
  start: string | null,
  end: string | null,
) {
  const key = start && end
    ? `${API_URL}/algo/regime/period-summary`
      + `?start=${start}&end=${end}`
    : null;
  const { data, error, isLoading } = useSWR<PeriodSummary>(
    key, fetcher,
    {
      // 60s dedup matches the backend cache TTL_VOLATILE band
      // and means a backfill landing mid-session refreshes the
      // chip within a minute of the next state change.
      revalidateOnFocus: true,
      dedupingInterval: 60_000,
    },
  );
  return {
    summary: data,
    error: error as Error | undefined,
    loading: isLoading,
  };
}

export function useClassifierHealth() {
  const { data, error, isLoading } = useSWR<ClassifierHealth>(
    `${API_URL}/algo/regime/classifier-health`,
    fetcher,
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );
  return {
    health: data ?? null,
    error: error instanceof Error ? error : undefined,
    loading: isLoading,
  };
}
