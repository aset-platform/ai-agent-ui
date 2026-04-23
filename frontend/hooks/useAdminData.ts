"use client";
/**
 * Data-fetching hooks for the Admin page.
 *
 * Uses SWR for caching so that switching between tabs
 * returns cached data instantly.  CRUD mutations call
 * ``mutate()`` to revalidate after writes.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  UserResponse,
  AuditEvent,
  MetricsResponse,
  TierHealthResponse,
  UserLLMKey,
} from "@/lib/types";

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------
// Users hook
// ---------------------------------------------------------------

export interface UseAdminUsersResult {
  users: UserResponse[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  createUser: (
    data: CreateUserData,
  ) => Promise<UserResponse | null>;
  updateUser: (
    userId: string,
    data: UpdateUserData,
  ) => Promise<UserResponse | null>;
  deactivateUser: (
    userId: string,
  ) => Promise<boolean>;
  reactivateUser: (
    userId: string,
  ) => Promise<boolean>;
  resetPassword: (
    userId: string,
    newPassword: string,
  ) => Promise<boolean>;
  uploadAvatar: (
    userId: string,
    file: File,
  ) => Promise<string | null>;
}

export interface CreateUserData {
  email: string;
  password: string;
  full_name: string;
  role: "superuser" | "pro" | "general";
}

export interface UpdateUserData {
  full_name?: string;
  email?: string;
  role?: "superuser" | "pro" | "general";
  is_active?: boolean;
  page_permissions?: Record<string, boolean>;
}

export function useAdminUsers(): UseAdminUsersResult {
  const { data, error, isLoading, mutate } =
    useSWR<UserResponse[]>(
      `${API_URL}/users`,
      fetcher,
      {
        revalidateOnFocus: false,
        dedupingInterval: 120_000,
      },
    );

  const users = data ?? [];
  const refresh = useCallback(
    () => {
      mutate();
    },
    [mutate],
  );

  const createUser = useCallback(
    async (
      body: CreateUserData,
    ): Promise<UserResponse | null> => {
      const r = await apiFetch(`${API_URL}/users`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      const user = await r.json();
      mutate();
      return user;
    },
    [mutate],
  );

  const updateUser = useCallback(
    async (
      userId: string,
      body: UpdateUserData,
    ): Promise<UserResponse | null> => {
      const r = await apiFetch(
        `${API_URL}/users/${userId}`,
        {
          method: "PATCH",
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
      const user = await r.json();
      mutate();
      return user;
    },
    [mutate],
  );

  const deactivateUser = useCallback(
    async (userId: string): Promise<boolean> => {
      const r = await apiFetch(
        `${API_URL}/users/${userId}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      mutate();
      return true;
    },
    [mutate],
  );

  const reactivateUser = useCallback(
    async (userId: string): Promise<boolean> => {
      const r = await apiFetch(
        `${API_URL}/users/${userId}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ is_active: true }),
        },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      mutate();
      return true;
    },
    [mutate],
  );

  const resetPassword = useCallback(
    async (
      userId: string,
      newPassword: string,
    ): Promise<boolean> => {
      const r = await apiFetch(
        `${API_URL}/users/${userId}/reset-password`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            new_password: newPassword,
          }),
        },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      return true;
    },
    [],
  );

  const uploadAvatar = useCallback(
    async (
      userId: string,
      file: File,
    ): Promise<string | null> => {
      const form = new FormData();
      form.append("file", file);
      const r = await apiFetch(
        `${API_URL}/auth/upload-avatar?user_id=${userId}`,
        { method: "POST", body: form },
      );
      if (!r.ok) return null;
      const b = await r.json();
      mutate();
      return b.avatar_url ?? null;
    },
    [mutate],
  );

  return {
    users,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load users"
      : null,
    refresh,
    createUser,
    updateUser,
    deactivateUser,
    reactivateUser,
    resetPassword,
    uploadAvatar,
  };
}

// ---------------------------------------------------------------
// Audit log hook
// ---------------------------------------------------------------

export interface UseAdminAuditResult {
  events: AuditEvent[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useAdminAudit(
  scope: "self" | "all" = "all",
): UseAdminAuditResult {
  const { data, error, isLoading, mutate } = useSWR<{
    events: AuditEvent[];
  }>(
    `${API_URL}/admin/audit-log?scope=${scope}`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 120_000,
    },
  );

  return {
    events: data?.events ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load audit log"
      : null,
    refresh: useCallback(
      () => {
        mutate();
      },
      [mutate],
    ),
  };
}

// ---------------------------------------------------------------
// LLM Observability hook
// ---------------------------------------------------------------

export interface UseObservabilityResult {
  metrics: MetricsResponse | null;
  health: TierHealthResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  toggleTier: (
    model: string,
    enabled: boolean,
  ) => Promise<void>;
}

interface ObsData {
  metrics: MetricsResponse;
  health: TierHealthResponse;
}

async function obsFetcher(
  url: string,
): Promise<ObsData> {
  // Cache key encodes the scope; parse it out so we can
  // skip superuser-only tier-health on self-scoped calls.
  const scope = url.includes("scope=self") ? "self" : "all";
  const metricsUrl = `${API_URL}/admin/metrics?scope=${scope}`;
  const fetches: Promise<unknown>[] = [
    apiFetch(metricsUrl).then((r) => {
      if (!r.ok)
        throw new Error(`metrics: HTTP ${r.status}`);
      return r.json();
    }),
  ];
  if (scope === "all") {
    fetches.push(
      apiFetch(`${API_URL}/admin/tier-health`).then(
        (r) => {
          if (!r.ok)
            throw new Error(`health: HTTP ${r.status}`);
          return r.json();
        },
      ),
    );
  }
  const results = await Promise.all(fetches);
  return {
    metrics: results[0] as MetricsResponse,
    health: (results[1] ?? null) as TierHealthResponse,
  };
}

export function useObservability(
  scope: "self" | "all" = "all",
): UseObservabilityResult {
  const { data, error, isLoading, mutate } =
    useSWR<ObsData>(
      `${API_URL}/admin/observability?scope=${scope}`,
      obsFetcher,
      {
        revalidateOnFocus: false,
        dedupingInterval: 30_000,
        refreshInterval: 60_000,
      },
    );

  const refresh = useCallback(
    () => {
      mutate();
    },
    [mutate],
  );

  const toggleTier = useCallback(
    async (model: string, enabled: boolean) => {
      const r = await apiFetch(
        `${API_URL}/admin/tier-toggle?model=${encodeURIComponent(model)}&enabled=${enabled}`,
        { method: "POST" },
      );
      if (!r.ok) {
        const b = await r.json().catch(
          () => ({}),
        );
        throw new Error(
          b.detail || `HTTP ${r.status}`,
        );
      }
      mutate();
    },
    [mutate],
  );

  return {
    metrics: data?.metrics ?? null,
    health: data?.health ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load observability"
      : null,
    refresh,
    toggleTier,
  };
}

/* ------------------------------------------------------------------ */
/* useAdminMaintenance — on-demand admin actions                       */
/* ------------------------------------------------------------------ */

export interface TriageEntry {
  sub_id: string;
  customer_id: string;
  status: string;
  classification: "matched" | "orphaned" | "unlinked";
  action: string;
}

export interface CleanupResult {
  triage: TriageEntry[];
  cleaned: number;
  dry_run: boolean;
  error?: string;
}

export interface RetentionResult {
  table: string;
  cutoff_date: string;
  rows_before: number;
  rows_deleted: number;
  dry_run: boolean;
  error: string | null;
}

export interface UsageUser {
  user_id: string;
  email: string;
  full_name: string;
  subscription_tier: string;
  monthly_usage_count: number;
}

export interface GapResult {
  top_gap_tickers: string[];
  external_api_usage: Record<string, number>;
  intent_distribution: Record<string, number>;
  local_sufficiency_rate: number;
}

export function useAdminMaintenance() {
  const cleanupSubscriptions = useCallback(
    async (dryRun: boolean): Promise<CleanupResult> => {
      const res = await apiFetch(
        `${API_URL}/subscription/cleanup?dry_run=${dryRun}`,
        { method: "POST" },
      );
      return res.json();
    },
    [],
  );

  const resetUsage = useCallback(
    async (): Promise<{ reset_count: number }> => {
      const res = await apiFetch(
        `${API_URL}/admin/reset-usage`,
        { method: "POST" },
      );
      return res.json();
    },
    [],
  );

  const getUsageStats = useCallback(
    async (
      scope: "self" | "all" = "all",
    ): Promise<{ users: UsageUser[] }> => {
      const res = await apiFetch(
        `${API_URL}/admin/usage-stats?scope=${scope}`,
      );
      return res.json();
    },
    [],
  );

  const resetSelectedUsage = useCallback(
    async (
      userIds: string[],
    ): Promise<{ reset_count: number }> => {
      const res = await apiFetch(
        `${API_URL}/admin/reset-usage/selected`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            user_ids: userIds,
          }),
        },
      );
      return res.json();
    },
    [],
  );

  const runRetention = useCallback(
    async (
      dryRun: boolean,
    ): Promise<{ results: RetentionResult[] }> => {
      const res = await apiFetch(
        `${API_URL}/admin/retention?dry_run=${dryRun}`,
        { method: "POST" },
      );
      return res.json();
    },
    [],
  );

  const analyzeGaps = useCallback(
    async (): Promise<GapResult> => {
      const res = await apiFetch(
        `${API_URL}/admin/query-gaps`,
      );
      return res.json();
    },
    [],
  );

  const retainSelected = useCallback(
    async (
      tableIds: string[],
    ): Promise<{ results: RetentionResult[] }> => {
      const res = await apiFetch(
        `${API_URL}/admin/retention/selected`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            table_ids: tableIds,
          }),
        },
      );
      return res.json();
    },
    [],
  );

  const getPaymentTransactions = useCallback(
    async (
      userId?: string,
      gw?: string,
    ): Promise<{
      transactions: Record<string, unknown>[];
    }> => {
      const params = new URLSearchParams();
      if (userId) params.set("user_id", userId);
      if (gw) params.set("gateway", gw);
      const res = await apiFetch(
        `${API_URL}/admin/payment-transactions?${params}`,
      );
      return res.json();
    },
    [],
  );

  return {
    cleanupSubscriptions,
    resetUsage,
    getUsageStats,
    resetSelectedUsage,
    runRetention,
    retainSelected,
    analyzeGaps,
    getPaymentTransactions,
  };
}

// ---------------------------------------------------------------
// Data Health hook
// ---------------------------------------------------------------

export interface DataHealthResult {
  total_registry: number;
  total_analyzable: number;
  total_financial: number;
  illiquid_count?: number;
  illiquid_tickers?: string[];
  ohlcv: {
    nan_close_count: number;
    nan_close_tickers: string[];
    missing_latest_count: number;
    stale_count: number;
    stale_tickers: string[];
  };
  forecasts: {
    total_tickers: number;
    missing_tickers: string[];
    extreme_predictions: number;
    high_mape: number;
    stale_count: number;
  };
  sentiment: {
    total_tickers: number;
    missing_tickers: string[];
    stale_count: number;
  };
  piotroski: {
    total_tickers: number;
    missing_tickers: string[];
    stale_count: number;
  };
  analytics: {
    total_tickers: number;
    missing_tickers: string[];
  };
}

export type FixTarget =
  | "ohlcv"
  | "analytics"
  | "sentiment"
  | "piotroski"
  | "forecasts";

export interface FixProgress {
  run_id: string;
  status: string;
  tickers_total: number;
  tickers_done: number;
  errors: string | null;
  elapsed_s: number | null;
}

export interface UseDataHealthResult {
  data: DataHealthResult | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  fixOhlcv: (
    action: "backfill_nan" | "backfill_missing",
  ) => Promise<{ status: string }>;
  triggerFix: (
    target: FixTarget,
    mode?: "stale_only" | "force_all",
  ) => Promise<void>;
  fixProgress: FixProgress | null;
  fixTarget: FixTarget | null;
}

export function useDataHealth(): UseDataHealthResult {
  const {
    data,
    error,
    isLoading,
    mutate,
  } = useSWR<DataHealthResult>(
    `${API_URL}/admin/data-health`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 5_000,
    },
  );

  const [fixTarget, setFixTarget] =
    useState<FixTarget | null>(null);
  const [fixProgress, setFixProgress] =
    useState<FixProgress | null>(null);
  const pollRef = useRef<ReturnType<
    typeof setInterval
  > | null>(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
    };
  }, []);

  const refresh = useCallback(
    () => {
      mutate(undefined, { revalidate: true });
    },
    [mutate],
  );

  const fixOhlcv = useCallback(
    async (
      action: "backfill_nan" | "backfill_missing",
    ): Promise<{ status: string }> => {
      const r = await apiFetch(
        `${API_URL}/admin/data-health/fix-ohlcv`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ action }),
        },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          (b as { detail?: string }).detail ||
            `HTTP ${r.status}`,
        );
      }
      const result = await r.json();
      mutate();
      return result as { status: string };
    },
    [mutate],
  );

  const triggerFix = useCallback(
    async (
      target: FixTarget,
      mode: "stale_only" | "force_all" = "stale_only",
    ) => {
      const r = await apiFetch(
        `${API_URL}/admin/data-health/fix`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ target, mode }),
        },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          (b as { detail?: string }).detail ||
            `HTTP ${r.status}`,
        );
      }
      const { run_id } = (await r.json()) as {
        run_id: string;
      };
      setFixTarget(target);
      setFixProgress({
        run_id,
        status: "running",
        tickers_total: 0,
        tickers_done: 0,
        errors: null,
        elapsed_s: null,
      });

      // Poll every 2s
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
      pollRef.current = setInterval(async () => {
        try {
          const sr = await apiFetch(
            `${API_URL}/admin/data-health/fix/${run_id}/status`,
          );
          if (!sr.ok) return;
          const p = (await sr.json()) as FixProgress;
          setFixProgress(p);
          const done = [
            "success",
            "failed",
            "cancelled",
          ].includes(p.status);
          if (done) {
            if (pollRef.current) {
              clearInterval(pollRef.current);
              pollRef.current = null;
            }
            mutate();
            setTimeout(() => {
              setFixTarget(null);
              setFixProgress(null);
            }, 5000);
          }
        } catch {
          /* ignore poll errors */
        }
      }, 2000);
    },
    [mutate],
  );

  return {
    data: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load data health"
      : null,
    refresh,
    fixOhlcv,
    triggerFix,
    fixProgress,
    fixTarget,
  };
}

// ---------------------------------------------------------------
// Admin Recommendations hook
// ---------------------------------------------------------------

export interface AdminRecommendationRow {
  id: string;
  run_id: string;
  user_id: string | null;
  email: string | null;
  full_name: string | null;
  ticker: string | null;
  tier: string;
  category: string;
  action: string;
  severity: string;
  rationale: string;
  expected_impact: string | null;
  data_signals: Record<string, number | string>;
  price_at_rec: number | null;
  target_price: number | null;
  expected_return_pct: number | null;
  index_tags: string[] | null;
  status: string;
  acted_on_date: string | null;
  created_at: string;
  scope: string;
  run_type: string;
  run_date: string | null;
}

interface AdminRecommendationsResponse {
  recommendations: AdminRecommendationRow[];
  count: number;
  limit: number;
  offset: number;
}

export interface UseAdminRecommendationsResult {
  recommendations: AdminRecommendationRow[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  deleteRecommendation: (id: string) => Promise<boolean>;
  deleteRecommendationRun: (
    runId: string,
  ) => Promise<boolean>;
  forceRefresh: (
    userId: string,
    scope: "india" | "us",
  ) => Promise<boolean>;
  promoteRun: (runId: string) => Promise<boolean>;
}

export function useAdminRecommendations():
  UseAdminRecommendationsResult {
  const { data, error, isLoading, mutate } =
    useSWR<AdminRecommendationsResponse>(
      `${API_URL}/admin/recommendations`,
      fetcher,
      { revalidateOnFocus: false },
    );

  const refresh = useCallback(() => {
    mutate(undefined, { revalidate: true });
  }, [mutate]);

  const deleteRecommendation = useCallback(
    async (id: string): Promise<boolean> => {
      const r = await apiFetch(
        `${API_URL}/admin/recommendations/${id}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          (b as { detail?: string }).detail ||
            `HTTP ${r.status}`,
        );
      }
      mutate();
      return true;
    },
    [mutate],
  );

  const deleteRecommendationRun = useCallback(
    async (runId: string): Promise<boolean> => {
      const r = await apiFetch(
        `${API_URL}/admin/recommendation-runs/${runId}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          (b as { detail?: string }).detail ||
            `HTTP ${r.status}`,
        );
      }
      mutate();
      return true;
    },
    [mutate],
  );

  const forceRefresh = useCallback(
    async (
      userId: string,
      scope: "india" | "us",
    ): Promise<boolean> => {
      const r = await apiFetch(
        `${API_URL}/admin/recommendations/force-refresh`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            user_id: userId,
            scope,
          }),
        },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          (b as { detail?: string }).detail ||
            `HTTP ${r.status}`,
        );
      }
      mutate();
      return true;
    },
    [mutate],
  );

  const promoteRun = useCallback(
    async (runId: string): Promise<boolean> => {
      const r = await apiFetch(
        `${API_URL}/admin/recommendation-runs/${runId}/promote`,
        { method: "POST" },
      );
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(
          (b as { detail?: string }).detail ||
            `HTTP ${r.status}`,
        );
      }
      mutate();
      return true;
    },
    [mutate],
  );

  return {
    recommendations: data?.recommendations ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load recommendations"
      : null,
    refresh,
    deleteRecommendation,
    deleteRecommendationRun,
    forceRefresh,
    promoteRun,
  };
}

// ---------------------------------------------------------------
// BYO provider keys (any authenticated user)
// ---------------------------------------------------------------

export interface UseUserLLMKeysResult {
  keys: UserLLMKey[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  saveKey: (
    provider: "groq" | "anthropic",
    key: string,
    label?: string | null,
  ) => Promise<void>;
  deleteKey: (
    provider: "groq" | "anthropic",
  ) => Promise<void>;
  updateLimit: (
    monthlyLimit: number,
  ) => Promise<void>;
}

export function useUserLLMKeys(): UseUserLLMKeysResult {
  const { data, error, isLoading, mutate } = useSWR<
    UserLLMKey[]
  >(
    `${API_URL}/users/me/llm-keys`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );

  const refresh = useCallback(() => {
    mutate();
  }, [mutate]);

  const saveKey = useCallback(
    async (
      provider: "groq" | "anthropic",
      key: string,
      label?: string | null,
    ) => {
      const r = await apiFetch(
        `${API_URL}/users/me/llm-keys/${provider}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            key,
            label: label || null,
          }),
        },
      );
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try {
          const body = await r.json();
          detail =
            (body as { detail?: string })?.detail || detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      mutate();
    },
    [mutate],
  );

  const deleteKey = useCallback(
    async (provider: "groq" | "anthropic") => {
      const r = await apiFetch(
        `${API_URL}/users/me/llm-keys/${provider}`,
        { method: "DELETE" },
      );
      // 204 = deleted, 404 = already gone (stale UI);
      // both resolve to the same client-side state.
      if (!r.ok && r.status !== 204 && r.status !== 404) {
        throw new Error(`HTTP ${r.status}`);
      }
      mutate();
    },
    [mutate],
  );

  const updateLimit = useCallback(
    async (monthlyLimit: number) => {
      const r = await apiFetch(
        `${API_URL}/users/me/byo-settings`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            monthly_limit: monthlyLimit,
          }),
        },
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    },
    [],
  );

  return {
    keys: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load keys"
      : null,
    refresh,
    saveKey,
    deleteKey,
    updateLimit,
  };
}
