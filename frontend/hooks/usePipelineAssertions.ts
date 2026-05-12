"use client";
/**
 * SWR hook for ``GET /admin/data-health/pipeline-assertions``
 * (ASETPLTFRM-380).
 *
 * Surfaces ``data_quality_violation`` events emitted by the
 * pipeline assertion framework. The admin Data Health card
 * renders these so silent-success pipeline runs (e.g. the
 * 2026-05-11 stale-VIX scenario) get flagged.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface PipelineAssertionRow {
  ts_ns: number;
  ts_date: string;
  pipeline_id: string | null;
  run_id: string | null;
  step: string | null;
  assertion: string | null;
  severity: "warn" | "error";
  message: string | null;
  detail: Record<string, unknown>;
  ts_ist: string | null;
}

interface ResponseShape {
  rows: PipelineAssertionRow[];
  counts: { warn?: number; error?: number };
}

async function fetcher(url: string): Promise<ResponseShape> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function usePipelineAssertions(
  opts: { days?: number; severity?: "warn" | "error" } = {},
) {
  const { days = 7, severity } = opts;
  const qs = new URLSearchParams({
    days: String(days),
    limit: "100",
  });
  if (severity) qs.set("severity", severity);
  const key = (
    `${API_URL}/admin/data-health/pipeline-assertions?${qs.toString()}`
  );
  const { data, error, isLoading, mutate } = useSWR<ResponseShape>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 60_000,
      dedupingInterval: 30_000,
    },
  );
  return {
    rows: data?.rows ?? [],
    counts: data?.counts ?? { warn: 0, error: 0 },
    loading: isLoading,
    error: error instanceof Error ? error.message : null,
    mutate,
  };
}
