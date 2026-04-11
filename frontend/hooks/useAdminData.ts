"use client";
/**
 * Data-fetching hooks for the Admin page.
 *
 * Uses SWR for caching so that switching between tabs
 * returns cached data instantly.  CRUD mutations call
 * ``mutate()`` to revalidate after writes.
 */

import { useCallback } from "react";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  UserResponse,
  AuditEvent,
  MetricsResponse,
  TierHealthResponse,
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
  role: "superuser" | "general";
}

export interface UpdateUserData {
  full_name?: string;
  email?: string;
  role?: "superuser" | "general";
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

export function useAdminAudit(): UseAdminAuditResult {
  const { data, error, isLoading, mutate } = useSWR<{
    events: AuditEvent[];
  }>(
    `${API_URL}/admin/audit-log`,
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

async function obsFetcher(): Promise<ObsData> {
  const [m, h] = await Promise.all([
    apiFetch(`${API_URL}/admin/metrics`).then(
      (r) => {
        if (!r.ok)
          throw new Error(
            `metrics: HTTP ${r.status}`,
          );
        return r.json();
      },
    ),
    apiFetch(`${API_URL}/admin/tier-health`).then(
      (r) => {
        if (!r.ok)
          throw new Error(
            `health: HTTP ${r.status}`,
          );
        return r.json();
      },
    ),
  ]);
  return {
    metrics: m as MetricsResponse,
    health: h as TierHealthResponse,
  };
}

export function useObservability(): UseObservabilityResult {
  const { data, error, isLoading, mutate } =
    useSWR<ObsData>(
      `${API_URL}/admin/observability`,
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
    async (): Promise<{ users: UsageUser[] }> => {
      const res = await apiFetch(
        `${API_URL}/admin/usage-stats`,
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

export interface UseDataHealthResult {
  data: DataHealthResult | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  fixOhlcv: (
    action: "backfill_nan" | "backfill_missing",
  ) => Promise<{ status: string }>;
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
  };
}
