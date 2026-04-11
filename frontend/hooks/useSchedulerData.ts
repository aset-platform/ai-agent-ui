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
  pipeline_run_id: string | null;
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

export interface PipelineStep {
  step_order: number;
  job_type: string;
  job_name: string;
  last_status: string | null;
  last_run_id: string | null;
  last_duration: number | null;
  error_message: string | null;
}

export interface Pipeline {
  pipeline_id: string;
  name: string;
  scope: string;
  enabled: boolean;
  cron_days: string[];
  cron_time: string;
  steps: PipelineStep[];
  is_running: boolean;
  last_pipeline_run_id: string | null;
  next_run: string | null;
  next_run_seconds: number | null;
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

export function useSchedulerPipelines() {
  const { data, error, isLoading, mutate } = useSWR<{
    pipelines: Pipeline[];
  }>(
    `${API_URL}/admin/scheduler/pipelines`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 10_000,
      refreshInterval: 15_000,
    },
  );
  return {
    pipelines: data?.pipelines ?? [],
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
    total: number;
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
    total: data?.total ?? 0,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load"
      : null,
    mutate,
  };
}

export function useSchedulerRunsFiltered(filters: {
  job_type?: string;
  status?: string;
  days?: number;
  offset?: number;
  limit?: number;
}) {
  const params = new URLSearchParams();
  if (filters.job_type)
    params.set("job_type", filters.job_type);
  if (filters.status)
    params.set("status", filters.status);
  if (filters.days)
    params.set("days", String(filters.days));
  if (filters.offset)
    params.set("offset", String(filters.offset));
  if (filters.limit)
    params.set("limit", String(filters.limit));
  const qs = params.toString();
  const url = `${API_URL}/admin/scheduler/runs${qs ? `?${qs}` : ""}`;

  const { data, error, isLoading, mutate } = useSWR<{
    runs: SchedulerRun[];
    total: number;
  }>(url, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 10_000,
    refreshInterval: 15_000,
  });
  return {
    runs: data?.runs ?? [],
    total: data?.total ?? 0,
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
        refreshInterval: 15_000,
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
