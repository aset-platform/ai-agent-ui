"use client";

import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export interface SchedulerJob {
  job_id: string;
  name: string;
  job_type: string;
  cron_days: string[];
  cron_dates: number[];
  cron_time: string;
  scope: string;
  enabled: boolean;
  next_run: string | null;
  next_run_seconds: number | null;
  last_run_status: string | null;
  last_run_time: string | null;
}

export interface SchedulerRun {
  run_id: string;
  job_id: string;
  job_name: string;
  job_type: string;
  scope: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  duration_secs: number | null;
  tickers_total: number;
  tickers_done: number;
  error_message: string | null;
  trigger_type: string | null;
}

export interface SchedulerStats {
  active_jobs: number;
  next_run_label: string | null;
  next_run_seconds: number | null;
  last_run_status: string | null;
  last_run_ago: string | null;
  last_run_tickers: number | null;
  runs_today: number;
  runs_today_success: number;
  runs_today_failed: number;
  runs_today_running: number;
}

export function useSchedulerJobs() {
  const { data, error, isLoading, mutate } = useSWR<{
    jobs: SchedulerJob[];
  }>(
    `${API_URL}/admin/scheduler/jobs`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 10_000,
    },
  );
  return {
    jobs: data?.jobs ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load"
      : null,
    mutate,
  };
}

export function useSchedulerRuns() {
  const { data, error, isLoading, mutate } = useSWR<{
    runs: SchedulerRun[];
  }>(
    `${API_URL}/admin/scheduler/runs`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 10_000,
    },
  );
  return {
    runs: data?.runs ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load"
      : null,
    mutate,
  };
}

export function useSchedulerStats() {
  const { data, error, isLoading, mutate } =
    useSWR<SchedulerStats>(
      `${API_URL}/admin/scheduler/stats`,
      fetcher,
      {
        revalidateOnFocus: false,
        dedupingInterval: 10_000,
      },
    );
  return {
    stats: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load"
      : null,
    mutate,
  };
}
