"use client";
/**
 * SWR hook for /v1/algo/strategies/*.
 *
 * - useStrategies(): list view; SWR-keyed on user implicitly
 *   via the cookie-bearing apiFetch.
 * - useStrategy(id): full AST fetch; lazy.
 * - createStrategy / updateStrategy / archiveStrategy:
 *   imperative wrappers that mutate the list cache.
 */

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export type StrategyMode = "draft" | "paper" | "live";

export interface StrategySummary {
  id: string;
  name: string;
  mode: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
  // Promotion-workflow fields surfaced by the list response.
  has_active_runtime?: boolean;
  active_runtime_modes?: string[];
  open_position_count?: number;
  has_ever_been_live?: boolean;
  last_transition_at?: string | null;
  last_transition_by?: string | null;
}

export interface TransitionEligibility {
  target: StrategyMode;
  allowed: boolean;
  reasons: string[];
  bypass_available: boolean;
}

export interface EligibilityResponse {
  current_mode: StrategyMode;
  transitions: TransitionEligibility[];
}

export interface ModeTransitionRow {
  id: string;
  from_mode: string | null;
  to_mode: string;
  reason: string | null;
  bypass_used: boolean;
  user_email: string;
  ast_hash: string | null;
  transitioned_at: string;
}

export interface StrategyAst {
  id: string;
  name: string;
  universe: unknown;
  schedule: unknown;
  rebalance: unknown;
  root: unknown;
  risk: unknown;
  // ASETPLTFRM-387 — added in support of MIS / intraday strategies.
  // Both fields are optional from the frontend's perspective; the
  // backend AST validator defaults product to "CNC" so existing
  // strategies remain valid without these keys.
  product?: "CNC" | "MIS";
  square_off_time?: string | null;
  // ASETPLTFRM-400 — "no new entries after" cutoff. MIS only;
  // CNC ignores it. Backend defaults to square_off − 60 min for
  // MIS strategies when the user leaves it null.
  entry_cutoff_time?: string | null;
}

const LIST_KEY = `${API_URL}/algo/strategies`;
const LIST_KEY_INCLUDE_ARCHIVED =
  `${API_URL}/algo/strategies?include_archived=true`;

async function mutateListCaches(): Promise<void> {
  // Mutations touch both the active-only and include-archived
  // caches so StrategiesTab + the strategy pickers re-sync after
  // create / update / clone / archive without an extra round-trip.
  await Promise.all([
    mutate(LIST_KEY),
    mutate(LIST_KEY_INCLUDE_ARCHIVED),
  ]);
}

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(
      `${url}: HTTP ${r.status}${detail ? ` — ${detail}` : ""}`,
    );
  }
  return r.json();
}

export function useStrategies(
  opts: { includeArchived?: boolean } = {},
) {
  // When ``includeArchived`` is on, the backend returns active +
  // archived rows; StrategiesTab uses this so users can filter via
  // the status select. All other call sites stay on the active-only
  // default, which avoids polluting strategy pickers with archived
  // entries.
  const key = opts.includeArchived
    ? `${LIST_KEY}?include_archived=true`
    : LIST_KEY;
  const { data, error, isLoading } = useSWR<{ strategies: StrategySummary[] }>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  return {
    strategies: data?.strategies ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load strategies"
      : null,
  };
}

export async function createStrategy(payload: StrategyAst): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/strategies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload }),
  });
  if (!r.ok) {
    throw new Error(`createStrategy: HTTP ${r.status}`);
  }
  const body = (await r.json()) as { id: string };
  await mutateListCaches();
  return body.id;
}

export async function updateStrategy(
  id: string,
  payload: StrategyAst,
): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload }),
  });
  if (!r.ok) {
    throw new Error(`updateStrategy: HTTP ${r.status}`);
  }
  await mutateListCaches();
}

export async function cloneStrategy(id: string): Promise<string> {
  const r = await apiFetch(
    `${API_URL}/algo/strategies/${id}/clone`,
    { method: "POST" },
  );
  if (!r.ok) {
    throw new Error(`cloneStrategy: HTTP ${r.status}`);
  }
  const body = (await r.json()) as { id: string };
  await mutateListCaches();
  return body.id;
}

/**
 * Filter a list of strategies down to the modes a given tab
 * permits. Used by the BacktestRunForm / PaperTab / DryRunTab /
 * LiveDashboard pickers so each tab only shows strategies that
 * are graduated to a stage where running them is meaningful.
 */
export function filterStrategiesByMode<
  T extends Pick<StrategySummary, "mode" | "archived_at">,
>(rows: T[], modes: StrategyMode[]): T[] {
  return rows.filter(
    (r) =>
      r.archived_at == null &&
      modes.includes((r.mode as StrategyMode) ?? "draft"),
  );
}

export async function fetchEligibility(
  id: string,
): Promise<EligibilityResponse> {
  const r = await apiFetch(
    `${API_URL}/algo/strategies/${id}/mode-transitions/eligibility`,
  );
  if (!r.ok) throw new Error(`fetchEligibility: HTTP ${r.status}`);
  return r.json();
}

export async function fetchTransitions(
  id: string,
): Promise<ModeTransitionRow[]> {
  const r = await apiFetch(
    `${API_URL}/algo/strategies/${id}/mode-transitions`,
  );
  if (!r.ok) throw new Error(`fetchTransitions: HTTP ${r.status}`);
  return r.json();
}

export async function setStrategyMode(
  id: string,
  body: { mode: StrategyMode; bypass?: boolean; reason?: string },
): Promise<{ mode: StrategyMode; transition_id: string }> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}/mode`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    // Surface the structured 409 missing-gates detail when present
    let detail = "";
    try {
      const payload = await r.json();
      detail =
        typeof payload?.detail === "string"
          ? payload.detail
          : typeof payload?.detail?.message === "string"
            ? `${payload.detail.message}: ${(payload.detail.missing ?? []).join("; ")}`
            : JSON.stringify(payload?.detail ?? payload);
    } catch {
      // ignore
    }
    throw new Error(
      `setStrategyMode: HTTP ${r.status}${detail ? ` — ${detail}` : ""}`,
    );
  }
  await mutateListCaches();
  return r.json();
}

export async function archiveStrategy(id: string): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`, {
    method: "DELETE",
  });
  if (!r.ok) {
    throw new Error(`archiveStrategy: HTTP ${r.status}`);
  }
  await mutateListCaches();
}
