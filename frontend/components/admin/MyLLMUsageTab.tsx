"use client";
/**
 * Pro / general user's "My LLM Usage" admin tab.
 *
 * Renders five sections:
 *   1. Free chat allowance (usage_count / 10 + banner when exhausted)
 *   2. Provider cards (Groq, Anthropic configurable; Ollama shared)
 *   3. Usage-this-month KPIs
 *   4. Usage-by-model table (CSV download)
 *   5. Daily trend sparkline
 *
 * The heavy lifting is on the backend; this file just shapes
 * the response from `GET /admin/metrics?scope=self` into UI.
 */

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  BYOProviderStatus,
  BYOQuota,
  DailyTrendPoint,
  MetricsResponse,
  UserModelUsage,
} from "@/lib/types";
import { useUserLLMKeys } from "@/hooks/useAdminData";
import { ConfigureProviderKeyModal } from "@/components/admin/ConfigureProviderKeyModal";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import { downloadCsv } from "@/lib/downloadCsv";

const METRICS_URL = `${API_URL}/admin/metrics?scope=self`;

async function fetchMetrics(url: string): Promise<MetricsResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function fmtInt(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
function fmtCost(n: number): string {
  if (n === 0) return "$0.00";
  if (n < 0.01) return "<$0.01";
  return `$${n.toFixed(2)}`;
}
function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const mins = Math.floor((Date.now() - d.getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return d.toISOString().slice(0, 10);
}

type Model = UserModelUsage & { name: string };

export function MyLLMUsageTab() {
  const { data, error, isLoading, mutate } = useSWR<MetricsResponse>(
    METRICS_URL,
    fetchMetrics,
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );
  const keys = useUserLLMKeys();

  const [modalProvider, setModalProvider] =
    useState<"groq" | "anthropic" | null>(null);
  const [deleteTarget, setDeleteTarget] =
    useState<"groq" | "anthropic" | null>(null);
  const [deleteError, setDeleteError] = useState<
    string | null
  >(null);

  const quota: BYOQuota | null = data?.quota ?? null;
  const providers: BYOProviderStatus[] = data?.providers ?? [];
  const daily: DailyTrendPoint[] = data?.daily_trend ?? [];
  const models = useMemo<Model[]>(
    () =>
      Object.entries(
        (data?.models ?? {}) as unknown as Record<
          string,
          UserModelUsage
        >,
      )
        .map(([name, m]) => ({ name, ...m }))
        .sort((a, b) => b.requests - a.requests),
    [data],
  );

  const totals = useMemo(() => {
    let requests = 0;
    let platform = 0;
    let userReq = 0;
    let cost = 0;
    let inTok = 0;
    let outTok = 0;
    for (const m of models) {
      requests += m.requests || 0;
      platform += m.requests_platform || 0;
      userReq += m.requests_user || 0;
      cost += m.cost || 0;
      inTok += m.input_tokens || 0;
      outTok += m.output_tokens || 0;
    }
    return {
      requests,
      platform,
      userReq,
      cost,
      inTok,
      outTok,
    };
  }, [models]);

  const avgLatency = Number(
    (
      (data?.cascade_stats as unknown as {
        usage?: { avg_latency_ms?: number };
      })?.usage?.avg_latency_ms ?? 0
    ).toFixed(0),
  );

  const freeUsed = quota?.free_allowance_used ?? 0;
  const freeTotal = quota?.free_allowance_total ?? 10;
  const freePct = Math.min(
    100,
    Math.round((freeUsed / Math.max(freeTotal, 1)) * 100),
  );
  const freeExhausted = freeUsed >= freeTotal;

  if (isLoading) {
    return (
      <div className="py-20 text-center text-gray-500">
        Loading your LLM usage…
      </div>
    );
  }
  if (error) {
    return (
      <div className="py-20 text-center text-red-600">
        Failed to load usage: {String(error)}
      </div>
    );
  }

  const csvRows = models.map((m) => ({
    model: m.name,
    provider: m.provider || "—",
    requests: m.requests,
    free_requests: m.requests_platform ?? 0,
    user_key_requests: m.requests_user ?? 0,
    input_tokens: m.input_tokens,
    output_tokens: m.output_tokens,
    cost_usd: m.cost.toFixed(4),
    last_used: m.last_used_at ?? "",
  }));

  return (
    <div className="space-y-6">
      {/* 1. Free allowance + BYO limit editor */}
      <section
        className="rounded-2xl border border-gray-200
          bg-white p-5 dark:border-gray-800
          dark:bg-gray-900/80"
      >
        <div className="flex items-baseline justify-between">
          <h3
            className="text-sm font-semibold
              text-gray-900 dark:text-gray-100"
          >
            Free chat allowance
          </h3>
          <span
            className="font-mono text-xs text-gray-500
              dark:text-gray-400"
          >
            {freeUsed} of {freeTotal} used
          </span>
        </div>
        <div
          className="mt-3 h-2 w-full overflow-hidden
            rounded-full bg-gray-100 dark:bg-gray-800"
        >
          <div
            className={`h-full rounded-full transition-all ${
              freeExhausted
                ? "bg-amber-500"
                : freePct > 60
                  ? "bg-yellow-500"
                  : "bg-emerald-500"
            }`}
            style={{ width: `${freePct}%` }}
          />
        </div>
        <p
          className="mt-3 text-xs text-gray-500
            dark:text-gray-400"
        >
          {freeExhausted
            ? "Your 10 free chat requests are exhausted. Configure a Groq or Anthropic key below to keep chatting."
            : "After 10 free chat requests you'll need to configure a Groq or Anthropic key below."}
        </p>

        {quota && (
          <BYOLimitEditor
            used={quota.byo_month_used}
            limit={quota.byo_monthly_limit}
            onSave={async (v) => {
              await keys.updateLimit(v);
              await mutate();
            }}
          />
        )}
      </section>

      {/* 2. Provider cards */}
      <section className="grid gap-4 md:grid-cols-3">
        {providers.map((p) => (
          <ProviderCard
            key={p.provider}
            status={p}
            onConfigure={(prov) => setModalProvider(prov)}
            onDelete={(prov) => {
              setDeleteError(null);
              setDeleteTarget(prov);
            }}
          />
        ))}
      </section>

      {/* 3. KPIs */}
      <section
        className="grid grid-cols-2 gap-3 md:grid-cols-4"
      >
        <Kpi
          label="Requests"
          value={fmtInt(totals.requests)}
          sub={`${fmtInt(totals.platform)} free · ${fmtInt(totals.userReq)} your keys`}
        />
        <Kpi
          label="Tokens"
          value={fmtTokens(totals.inTok + totals.outTok)}
          sub={`${fmtTokens(totals.inTok)} in · ${fmtTokens(totals.outTok)} out`}
        />
        <Kpi
          label="Estimated cost"
          value={fmtCost(totals.cost)}
          sub="platform covered"
        />
        <Kpi
          label="Avg latency"
          value={`${avgLatency || 0} ms`}
        />
      </section>

      {/* 4. Usage by model */}
      <section
        className="rounded-2xl border border-gray-200
          bg-white dark:border-gray-800
          dark:bg-gray-900/80"
      >
        <div
          className="flex items-center justify-between
            border-b border-gray-100 px-4 py-3
            dark:border-gray-800"
        >
          <h3
            className="text-sm font-semibold
              text-gray-900 dark:text-gray-100"
          >
            Usage by model · last 30 days
          </h3>
          <DownloadCsvButton
            disabled={csvRows.length === 0}
            onClick={() =>
              downloadCsv(
                csvRows,
                [
                  { key: "model", header: "Model" },
                  { key: "provider", header: "Provider" },
                  { key: "requests", header: "Requests" },
                  {
                    key: "free_requests",
                    header: "Free (platform)",
                  },
                  {
                    key: "user_key_requests",
                    header: "Your keys",
                  },
                  {
                    key: "input_tokens",
                    header: "Input tokens",
                  },
                  {
                    key: "output_tokens",
                    header: "Output tokens",
                  },
                  { key: "cost_usd", header: "Cost (USD)" },
                  { key: "last_used", header: "Last used" },
                ],
                "my_llm_usage_by_model",
              )
            }
          />
        </div>
        {models.length === 0 ? (
          <p
            className="px-4 py-8 text-center text-sm
              text-gray-500 dark:text-gray-400"
          >
            No LLM usage yet — start a chat to populate this.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead
              className="text-xs uppercase tracking-wide
                text-gray-500 dark:text-gray-400"
            >
              <tr>
                <th className="px-4 py-2 text-left">Model</th>
                <th className="px-4 py-2 text-left">
                  Provider
                </th>
                <th className="px-4 py-2 text-right">
                  Requests
                </th>
                <th className="px-4 py-2 text-right">
                  Free · Your keys
                </th>
                <th className="px-4 py-2 text-right">
                  Tokens
                </th>
                <th className="px-4 py-2 text-right">Cost</th>
                <th className="px-4 py-2 text-right">
                  Last used
                </th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr
                  key={m.name}
                  className="border-t border-gray-50
                    text-gray-800 dark:border-gray-800
                    dark:text-gray-200"
                >
                  <td className="px-4 py-2 font-mono text-xs">
                    {m.name}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    <span
                      className="rounded-md bg-gray-100
                        px-1.5 py-0.5 font-medium
                        text-gray-700
                        dark:bg-gray-800
                        dark:text-gray-300"
                    >
                      {m.provider || "—"}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    {fmtInt(m.requests)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span
                      className="inline-flex items-center
                        gap-1.5 font-mono text-xs"
                    >
                      <span
                        className="rounded bg-sky-100 px-1.5
                          py-0.5 text-sky-700
                          dark:bg-sky-500/15
                          dark:text-sky-300"
                        title="Platform-provided (free)"
                      >
                        {fmtInt(m.requests_platform ?? 0)}
                      </span>
                      <span className="text-gray-400">·</span>
                      <span
                        className="rounded bg-emerald-100 px-1.5
                          py-0.5 text-emerald-700
                          dark:bg-emerald-500/15
                          dark:text-emerald-300"
                        title="Your configured keys"
                      >
                        {fmtInt(m.requests_user ?? 0)}
                      </span>
                    </span>
                  </td>
                  <td
                    className="px-4 py-2 text-right
                      text-xs text-gray-600
                      dark:text-gray-400"
                  >
                    {fmtTokens(
                      m.input_tokens + m.output_tokens,
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {fmtCost(m.cost)}
                  </td>
                  <td
                    className="px-4 py-2 text-right
                      text-xs text-gray-500
                      dark:text-gray-400"
                  >
                    {fmtRelative(m.last_used_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* 5. Trend */}
      {daily.length > 0 && (
        <section
          className="rounded-2xl border border-gray-200
            bg-white p-4 dark:border-gray-800
            dark:bg-gray-900/80"
        >
          <div className="flex items-center justify-between">
            <h3
              className="text-sm font-semibold
                text-gray-900 dark:text-gray-100"
            >
              Daily trend · last 30 days
            </h3>
            <span
              className="font-mono text-xs text-gray-400
                dark:text-gray-500"
            >
              {daily.length} day
              {daily.length !== 1 ? "s" : ""} with activity
            </span>
          </div>
          <Sparkline points={daily} />
        </section>
      )}

      <ConfigureProviderKeyModal
        open={modalProvider !== null}
        provider={modalProvider ?? "groq"}
        existingLabel={
          providers.find(
            (p) => p.provider === modalProvider,
          )?.label
        }
        onSave={async (k, label) => {
          if (modalProvider === null) return;
          await keys.saveKey(modalProvider, k, label);
          mutate();
        }}
        onClose={() => setModalProvider(null)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title={
          deleteTarget
            ? `Delete ${deleteTarget === "groq" ? "Groq" : "Anthropic"} key?`
            : "Delete key?"
        }
        message={
          "Your chat will stop routing through this provider. "
          + "Once you exhaust your free allowance with no key "
          + "configured, chat will be blocked until you add "
          + "one again."
        }
        confirmLabel="Delete key"
        cancelLabel="Keep it"
        variant="danger"
        onCancel={() => {
          setDeleteTarget(null);
          setDeleteError(null);
        }}
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await keys.deleteKey(deleteTarget);
            await mutate();
            setDeleteTarget(null);
          } catch (e) {
            setDeleteError(
              e instanceof Error
                ? e.message
                : "Delete failed",
            );
          }
        }}
      />
      {deleteError && (
        <p
          className="text-right text-xs text-red-600
            dark:text-red-400"
        >
          {deleteError}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------

function Kpi({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div
      className="rounded-2xl border border-gray-200
        bg-white p-4 dark:border-gray-800
        dark:bg-gray-900/80"
    >
      <div
        className="text-[11px] font-medium uppercase
          tracking-wide text-gray-500
          dark:text-gray-400"
      >
        {label}
      </div>
      <div
        className="mt-1 text-2xl font-semibold
          text-gray-900 dark:text-gray-100"
      >
        {value}
      </div>
      {sub && (
        <div
          className="mt-1 text-xs text-gray-500
            dark:text-gray-400"
        >
          {sub}
        </div>
      )}
    </div>
  );
}

function ProviderCard({
  status,
  onConfigure,
  onDelete,
}: {
  status: BYOProviderStatus;
  onConfigure: (p: "groq" | "anthropic") => void;
  onDelete: (p: "groq" | "anthropic") => void;
}) {
  const meta: Record<
    string,
    { title: string; blurb: string }
  > = {
    groq: {
      title: "Groq",
      blurb: "Fast open-weight models — your own bill",
    },
    anthropic: {
      title: "Anthropic",
      blurb: "Claude family — your own bill",
    },
    ollama: {
      title: "Ollama (shared)",
      blurb: "Native fallback — free for everyone",
    },
  };
  const m = meta[status.provider] ?? {
    title: status.provider,
    blurb: "",
  };
  const configurable =
    status.provider === "groq" ||
    status.provider === "anthropic";

  return (
    <div
      className="rounded-2xl border border-gray-200
        bg-white p-4 dark:border-gray-800
        dark:bg-gray-900/80"
    >
      <div className="flex items-center justify-between">
        <h4
          className="text-sm font-semibold
            text-gray-900 dark:text-gray-100"
        >
          {m.title}
        </h4>
        <StatusBadge
          configured={status.configured}
          native={Boolean(status.native)}
        />
      </div>
      <p
        className="mt-1 text-xs text-gray-500
          dark:text-gray-400"
      >
        {m.blurb}
      </p>

      {configurable && status.configured && (
        <dl
          className="mt-3 space-y-1 text-xs
            text-gray-700 dark:text-gray-300"
        >
          <div className="flex justify-between">
            <dt className="text-gray-500 dark:text-gray-400">
              Key
            </dt>
            <dd className="font-mono">
              {status.masked_key || "****"}
            </dd>
          </div>
          {status.label && (
            <div className="flex justify-between">
              <dt className="text-gray-500 dark:text-gray-400">
                Label
              </dt>
              <dd>{status.label}</dd>
            </div>
          )}
          <div className="flex justify-between">
            <dt className="text-gray-500 dark:text-gray-400">
              Last used
            </dt>
            <dd>
              {fmtRelative(status.last_used_at)}
            </dd>
          </div>
        </dl>
      )}

      {configurable && (
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            onClick={() =>
              onConfigure(
                status.provider as "groq" | "anthropic",
              )
            }
            className="rounded-lg bg-indigo-600 px-3 py-1.5
              text-xs font-semibold text-white
              hover:bg-indigo-700"
          >
            {status.configured ? "Edit key" : "+ Add key"}
          </button>
          {status.configured && (
            <button
              type="button"
              onClick={() =>
                onDelete(
                  status.provider as "groq" | "anthropic",
                )
              }
              className="rounded-lg border
                border-gray-300 px-3 py-1.5 text-xs
                font-medium text-gray-600
                hover:bg-gray-50 dark:border-gray-700
                dark:text-gray-300
                dark:hover:bg-gray-800"
            >
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function StatusBadge({
  configured,
  native,
}: {
  configured: boolean;
  native: boolean;
}) {
  if (native) {
    return (
      <span
        className="rounded-full bg-sky-100 px-2 py-0.5
          text-[11px] font-medium text-sky-700
          dark:bg-sky-500/15 dark:text-sky-300"
      >
        native
      </span>
    );
  }
  if (configured) {
    return (
      <span
        className="rounded-full bg-emerald-100 px-2 py-0.5
          text-[11px] font-medium text-emerald-700
          dark:bg-emerald-500/15 dark:text-emerald-300"
      >
        ● configured
      </span>
    );
  }
  return (
    <span
      className="rounded-full bg-gray-100 px-2 py-0.5
        text-[11px] font-medium text-gray-600
        dark:bg-gray-800 dark:text-gray-400"
    >
      not set
    </span>
  );
}

function Sparkline({
  points,
}: {
  points: DailyTrendPoint[];
}) {
  const W = 400;
  const H = 60;
  const max = Math.max(1, ...points.map((p) => p.requests));
  const dx = W / Math.max(points.length - 1, 1);
  const path = points
    .map((p, i) => {
      const x = i * dx;
      const y = H - (p.requests / max) * (H - 4) - 2;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="mt-3 h-16 w-full text-indigo-500"
    >
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function BYOLimitEditor({
  used,
  limit,
  onSave,
}: {
  used: number;
  limit: number;
  onSave: (v: number) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(limit);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(limit);
  }, [limit]);

  const pct = limit > 0
    ? Math.min(100, Math.round((used / limit) * 100))
    : 0;
  const bad = used >= limit;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(draft);
      setEditing(false);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Save failed",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="mt-4 border-t border-gray-100 pt-4
        dark:border-gray-800"
    >
      <div className="flex items-baseline justify-between">
        <h4
          className="text-xs font-semibold uppercase
            tracking-wide text-gray-500
            dark:text-gray-400"
        >
          Your monthly limit (BYO keys)
        </h4>
        {!editing ? (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-xs font-medium text-indigo-600
              hover:text-indigo-700
              dark:text-indigo-400"
          >
            Edit
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={1_000_000}
              value={draft}
              onChange={(e) =>
                setDraft(
                  Math.max(
                    0,
                    parseInt(e.target.value, 10) || 0,
                  ),
                )
              }
              className="w-20 rounded-md border
                border-gray-300 bg-white px-2 py-1
                text-right font-mono text-xs
                dark:border-gray-700
                dark:bg-gray-800
                dark:text-gray-100"
            />
            <button
              type="button"
              disabled={saving || draft === limit}
              onClick={save}
              className="rounded-md bg-indigo-600 px-2 py-1
                text-xs font-semibold text-white
                hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(limit);
                setEditing(false);
                setError(null);
              }}
              className="text-xs text-gray-500
                hover:text-gray-700
                dark:text-gray-400
                dark:hover:text-gray-200"
            >
              Cancel
            </button>
          </div>
        )}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span
          className="font-mono text-xs text-gray-600
            dark:text-gray-400"
        >
          {used} of {limit} chat turns used
        </span>
        {bad && (
          <span className="text-xs text-amber-600">
            limit reached — chat will be blocked
          </span>
        )}
      </div>
      <div
        className="mt-1 h-1.5 w-full overflow-hidden
          rounded-full bg-gray-100 dark:bg-gray-800"
      >
        <div
          className={`h-full rounded-full transition-all ${
            bad
              ? "bg-amber-500"
              : pct > 75
                ? "bg-yellow-500"
                : "bg-indigo-500"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {error && (
        <p className="mt-2 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
