"use client";
/**
 * Admin Recommendations tab — cross-user listing
 * grouped by recommendation run. Each run row
 * expands to reveal its child recommendations;
 * admins can delete a whole run (cascade) or a
 * single recommendation.
 */

import {
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  useAdminRecommendations,
  type AdminRecommendationRow,
} from "@/hooks/useAdminData";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { downloadCsv } from "@/lib/downloadCsv";
import { DownloadCsvButton } from "@/components/common/DownloadCsvButton";
import {
  ScopeBadge,
  RunTypeBadge,
  TierBadge,
  SeverityPill,
  CategoryPill,
  healthBadgeClass,
} from "@/components/recommendations/badges";

interface RunGroup {
  run_id: string;
  user_id: string | null;
  email: string | null;
  full_name: string | null;
  scope: string;
  run_type: string;
  run_date: string | null;
  earliest: string;
  recs: AdminRecommendationRow[];
  acted_on: number;
}

function groupByRun(
  rows: AdminRecommendationRow[],
): RunGroup[] {
  const map = new Map<string, RunGroup>();
  for (const r of rows) {
    const g = map.get(r.run_id);
    if (g) {
      g.recs.push(r);
      if (r.acted_on_date) g.acted_on += 1;
      if (r.created_at < g.earliest)
        g.earliest = r.created_at;
    } else {
      map.set(r.run_id, {
        run_id: r.run_id,
        user_id: r.user_id,
        email: r.email,
        full_name: r.full_name,
        scope: r.scope,
        run_type: r.run_type,
        run_date: r.run_date,
        earliest: r.created_at,
        recs: [r],
        acted_on: r.acted_on_date ? 1 : 0,
      });
    }
  }
  return Array.from(map.values()).sort((a, b) =>
    a.earliest < b.earliest ? 1 : -1,
  );
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className={`h-4 w-4 transition-transform ${
        open ? "rotate-180" : ""
      }`}
    >
      <path
        fillRule="evenodd"
        d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193v-.443A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path d="M10 12.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z" />
      <path
        fillRule="evenodd"
        d="M.664 10.59a1.651 1.651 0 010-1.186A10.004 10.004 0 0110 3c4.257 0 7.893 2.66 9.336 6.41.147.382.147.804 0 1.186A10.004 10.004 0 0110 17c-4.257 0-7.893-2.66-9.336-6.41zM14 10a4 4 0 11-8 0 4 4 0 018 0z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function RationaleModal({
  row,
  onClose,
}: {
  row: AdminRecommendationRow | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!row) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () =>
      document.removeEventListener("keydown", handler);
  }, [row, onClose]);

  if (!row) return null;

  const signals = Object.entries(row.data_signals ?? {});

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl mx-4 max-h-[80vh] overflow-auto rounded-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              {row.ticker ?? "Recommendation"}
              <span className="ml-2 text-sm font-normal text-gray-500 dark:text-gray-400">
                {row.action.toUpperCase()}
              </span>
            </h3>
            <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
              {row.full_name ?? row.email ?? row.user_id}
              {" · "}
              {row.tier} · {row.category} · {row.severity}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-200"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              className="h-5 w-5"
            >
              <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
            </svg>
          </button>
        </div>

        <div className="mt-4 space-y-3">
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Rationale
            </h4>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-gray-700 dark:text-gray-300">
              {row.rationale}
            </p>
          </section>

          {row.expected_impact && (
            <section>
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Expected Impact
              </h4>
              <p className="mt-1 text-sm italic text-gray-600 dark:text-gray-400">
                {row.expected_impact}
              </p>
            </section>
          )}

          {signals.length > 0 && (
            <section>
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Data Signals
              </h4>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {signals.map(([k, v]) => (
                  <span
                    key={k}
                    className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-200"
                  >
                    {k.replace(/_/g, " ")}:{" "}
                    <span className="font-semibold">
                      {typeof v === "number"
                        ? v.toFixed(2)
                        : String(v)}
                    </span>
                  </span>
                ))}
              </div>
            </section>
          )}

          {(row.price_at_rec != null ||
            row.target_price != null ||
            row.expected_return_pct != null) && (
            <section>
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Pricing
              </h4>
              <dl className="mt-1 grid grid-cols-3 gap-3 text-xs">
                {row.price_at_rec != null && (
                  <div>
                    <dt className="text-gray-500 dark:text-gray-400">
                      Price at rec
                    </dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {row.price_at_rec.toLocaleString()}
                    </dd>
                  </div>
                )}
                {row.target_price != null && (
                  <div>
                    <dt className="text-gray-500 dark:text-gray-400">
                      Target
                    </dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {row.target_price.toLocaleString()}
                    </dd>
                  </div>
                )}
                {row.expected_return_pct != null && (
                  <div>
                    <dt className="text-gray-500 dark:text-gray-400">
                      Expected return
                    </dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {row.expected_return_pct.toFixed(2)}%
                    </dd>
                  </div>
                )}
              </dl>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Child recommendation row ─────────────────── */

function ChildRecRow({
  rec,
  onView,
  onDelete,
}: {
  rec: AdminRecommendationRow;
  onView: (r: AdminRecommendationRow) => void;
  onDelete: (r: AdminRecommendationRow) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900/60">
      <span className="min-w-[90px] font-mono text-xs font-semibold text-gray-900 dark:text-gray-100">
        {rec.ticker ?? "—"}
      </span>
      <TierBadge tier={rec.tier} />
      <CategoryPill category={rec.category} />
      <SeverityPill severity={rec.severity} />
      <span className="text-[11px] font-medium uppercase text-gray-600 dark:text-gray-400">
        {rec.action}
      </span>
      <span
        className="ml-1 max-w-md flex-1 truncate text-[11px] text-gray-500 dark:text-gray-400"
        title={rec.rationale}
      >
        {rec.rationale}
      </span>
      <div className="ml-auto flex items-center gap-1">
        <button
          onClick={() => onView(rec)}
          className="rounded-md bg-gray-100 p-1.5 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
          aria-label={`View rationale ${rec.id}`}
          title="View rationale"
        >
          <EyeIcon />
        </button>
        <button
          onClick={() => onDelete(rec)}
          className="rounded-md bg-red-50 p-1.5 text-red-700 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40"
          aria-label={`Delete recommendation ${rec.id}`}
          title="Delete this recommendation"
        >
          <TrashIcon />
        </button>
      </div>
    </div>
  );
}

/* ── Run row (collapsible) ─────────────────────── */

function RunRow({
  group,
  open,
  onToggle,
  onDeleteRun,
  onView,
  onDeleteRec,
  onPromoteRun,
}: {
  group: RunGroup;
  open: boolean;
  onToggle: () => void;
  onDeleteRun: (g: RunGroup) => void;
  onView: (r: AdminRecommendationRow) => void;
  onDeleteRec: (r: AdminRecommendationRow) => void;
  onPromoteRun: (g: RunGroup) => void;
}) {
  const healthCls = healthBadgeClass(
    // run has no explicit score in flat payload; derive
    // "needs_attention" style neutral chip
    50,
  );
  return (
    <div className="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900/60">
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        className="flex w-full cursor-pointer flex-wrap items-center gap-3 px-4 py-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        aria-expanded={open}
      >
        <span className="whitespace-nowrap text-sm font-medium text-gray-900 dark:text-gray-100">
          {formatTimestamp(group.earliest)}
        </span>
        <div className="flex min-w-[180px] flex-col leading-tight">
          <span className="text-sm text-gray-900 dark:text-gray-100">
            {group.full_name ?? "—"}
          </span>
          <span className="font-mono text-[11px] text-gray-500 dark:text-gray-400">
            {group.email ?? group.user_id ?? "unknown"}
          </span>
        </div>
        <ScopeBadge scope={group.scope} />
        <RunTypeBadge runType={group.run_type} />
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${healthCls}`}
        >
          {group.recs.length} recs
        </span>
        <span className="text-[11px] text-gray-500 dark:text-gray-400">
          {group.acted_on} acted on
        </span>
        <div className="ml-auto flex items-center gap-1">
          {group.run_type === "admin_test" && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onPromoteRun(group);
              }}
              className="inline-flex items-center gap-1 rounded-md bg-fuchsia-50 px-2 py-1 text-[11px] font-medium text-fuchsia-700 hover:bg-fuchsia-100 dark:bg-fuchsia-900/20 dark:text-fuchsia-400 dark:hover:bg-fuchsia-900/40"
              aria-label={`Replace with run ${group.run_id}`}
              title="Replace the user's current run with this test run"
            >
              Replace
            </button>
          )}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDeleteRun(group);
            }}
            className="inline-flex items-center gap-1 rounded-md bg-red-50 px-2 py-1 text-[11px] font-medium text-red-700 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40"
            aria-label={`Delete run ${group.run_id}`}
            title="Delete whole run"
          >
            <TrashIcon />
            Delete run
          </button>
          <ChevronIcon open={open} />
        </div>
      </div>
      {open && (
        <div className="space-y-1.5 border-t border-gray-200 px-4 py-3 dark:border-gray-700">
          <div className="mb-2 flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400">
            <span>
              Run ID:{" "}
              <span className="font-mono text-gray-700 dark:text-gray-300">
                {group.run_id}
              </span>
            </span>
            {group.run_date && (
              <span>
                Run Date:{" "}
                <span className="text-gray-700 dark:text-gray-300">
                  {group.run_date}
                </span>
              </span>
            )}
            <span>
              Adoption:{" "}
              <span className="text-gray-700 dark:text-gray-300">
                {group.recs.length > 0
                  ? `${Math.round(
                      (group.acted_on /
                        group.recs.length) *
                        100,
                    )}%`
                  : "0%"}{" "}
                ({group.acted_on}/{group.recs.length})
              </span>
            </span>
          </div>
          {group.recs.map((rec) => (
            <ChildRecRow
              key={rec.id}
              rec={rec}
              onView={onView}
              onDelete={onDeleteRec}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Main tab ─────────────────────────────────── */

export function RecommendationsTab() {
  const {
    recommendations,
    loading,
    error,
    refresh,
    deleteRecommendation,
    deleteRecommendationRun,
    forceRefresh,
    promoteRun,
  } = useAdminRecommendations();

  const [expanded, setExpanded] = useState<
    Set<string>
  >(new Set());
  const [pendingDeleteRec, setPendingDeleteRec] =
    useState<AdminRecommendationRow | null>(null);
  const [pendingDeleteRun, setPendingDeleteRun] =
    useState<RunGroup | null>(null);
  const [pendingPromoteRun, setPendingPromoteRun] =
    useState<RunGroup | null>(null);
  const [viewRow, setViewRow] =
    useState<AdminRecommendationRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [forceUserId, setForceUserId] = useState("");
  const [forceScope, setForceScope] = useState<
    "india" | "us"
  >("india");
  const [forceBusy, setForceBusy] = useState(false);
  const [forceMsg, setForceMsg] = useState<
    { kind: "ok" | "err"; text: string } | null
  >(null);

  const groups = useMemo(
    () => groupByRun(recommendations),
    [recommendations],
  );

  const filteredGroups = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter((g) => {
      const hay = [
        g.email,
        g.full_name,
        g.scope,
        g.run_type,
        ...g.recs.map((r) => r.ticker ?? ""),
        ...g.recs.map((r) => r.category),
        ...g.recs.map((r) => r.tier),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [groups, filter]);

  const maxPages = Math.max(
    1,
    Math.ceil(filteredGroups.length / pageSize),
  );
  const safePage = Math.min(page, maxPages);
  const pagedGroups = useMemo(
    () =>
      filteredGroups.slice(
        (safePage - 1) * pageSize,
        safePage * pageSize,
      ),
    [filteredGroups, safePage, pageSize],
  );

  // Reset to page 1 whenever the filter changes
  useEffect(() => {
    setPage(1);
  }, [filter]);

  const toggle = (runId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  };

  const confirmDeleteRec = async () => {
    if (!pendingDeleteRec) return;
    setBusy(true);
    try {
      await deleteRecommendation(pendingDeleteRec.id);
    } catch (e) {
      console.error(e);
    }
    setBusy(false);
    setPendingDeleteRec(null);
  };

  const confirmDeleteRun = async () => {
    if (!pendingDeleteRun) return;
    setBusy(true);
    try {
      await deleteRecommendationRun(
        pendingDeleteRun.run_id,
      );
    } catch (e) {
      console.error(e);
    }
    setBusy(false);
    setPendingDeleteRun(null);
  };

  const confirmPromoteRun = async () => {
    if (!pendingPromoteRun) return;
    setBusy(true);
    try {
      await promoteRun(pendingPromoteRun.run_id);
    } catch (e) {
      console.error(e);
      setForceMsg({
        kind: "err",
        text:
          e instanceof Error
            ? e.message
            : "Promote failed",
      });
    }
    setBusy(false);
    setPendingPromoteRun(null);
  };

  const handleForceRefresh = async () => {
    const uid = forceUserId.trim();
    if (!uid) {
      setForceMsg({
        kind: "err",
        text: "User ID is required.",
      });
      return;
    }
    setForceBusy(true);
    setForceMsg(null);
    try {
      await forceRefresh(uid, forceScope);
      setForceMsg({
        kind: "ok",
        text: `Test run generated for ${forceScope}.`,
      });
      setForceUserId("");
    } catch (e) {
      setForceMsg({
        kind: "err",
        text:
          e instanceof Error
            ? e.message
            : "Force refresh failed",
      });
    } finally {
      setForceBusy(false);
    }
  };

  const handleDownload = () => {
    const rows = filteredGroups.flatMap((g) =>
      g.recs.map((r) => ({
        created_at: r.created_at,
        run_date: g.run_date ?? "",
        run_type: g.run_type,
        scope: g.scope,
        full_name: g.full_name ?? "",
        email: g.email ?? "",
        user_id: g.user_id ?? "",
        run_id: g.run_id,
        ticker: r.ticker ?? "",
        tier: r.tier,
        category: r.category,
        severity: r.severity,
        action: r.action,
        rationale: r.rationale,
        expected_impact: r.expected_impact ?? "",
        price_at_rec: r.price_at_rec ?? "",
        target_price: r.target_price ?? "",
        expected_return_pct: r.expected_return_pct ?? "",
        acted_on_date: r.acted_on_date ?? "",
        status: r.status,
      })),
    );
    if (rows.length === 0) return;
    downloadCsv(
      rows,
      [
        { key: "created_at", header: "Created" },
        { key: "run_date", header: "Run Date" },
        { key: "run_type", header: "Run Type" },
        { key: "scope", header: "Scope" },
        { key: "full_name", header: "Full Name" },
        { key: "email", header: "Email" },
        { key: "user_id", header: "User ID" },
        { key: "run_id", header: "Run ID" },
        { key: "ticker", header: "Ticker" },
        { key: "tier", header: "Tier" },
        { key: "category", header: "Category" },
        { key: "severity", header: "Severity" },
        { key: "action", header: "Action" },
        { key: "rationale", header: "Rationale" },
        { key: "expected_impact", header: "Expected Impact" },
        { key: "price_at_rec", header: "Price at Rec" },
        { key: "target_price", header: "Target Price" },
        {
          key: "expected_return_pct",
          header: "Expected Return %",
        },
        { key: "acted_on_date", header: "Acted On" },
        { key: "status", header: "Status" },
      ],
      "admin-recommendations",
    );
  };

  if (error) {
    return (
      <div className="rounded-2xl border border-red-200 bg-white p-5 dark:border-red-800 dark:bg-gray-900/80">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-[15px] font-bold text-red-700 dark:text-red-400">
              Failed to load recommendations
            </h3>
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          </div>
          <button
            onClick={refresh}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const totalRecs = recommendations.length;
  const totalRuns = groups.length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-bold text-gray-900 dark:text-gray-100">
            Recommendations
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {loading
              ? "Loading…"
              : `${filteredGroups.length} of ${totalRuns} runs · ${totalRecs} total recs across all users`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by email, ticker, tier…"
            className="w-64 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-900 placeholder:text-gray-400 focus:border-indigo-500 focus:outline-none dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          />
          <button
            onClick={refresh}
            disabled={loading}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-amber-300 bg-amber-50/60 px-4 py-3 dark:border-amber-800 dark:bg-amber-900/10">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-col">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-400">
              Force-refresh (test run)
            </span>
            <span className="text-[11px] text-amber-700 dark:text-amber-300/80">
              Bypasses the monthly quota. The result is
              labelled TEST and stays hidden from the
              user until you Replace.
            </span>
          </div>
          <input
            type="text"
            value={forceUserId}
            onChange={(e) =>
              setForceUserId(e.target.value)
            }
            placeholder="User email or UUID"
            className="w-72 rounded-md border border-amber-300 bg-white px-2 py-1 text-xs text-gray-900 placeholder:text-gray-400 focus:border-amber-500 focus:outline-none dark:border-amber-700 dark:bg-gray-900 dark:text-gray-100"
          />
          <select
            value={forceScope}
            onChange={(e) =>
              setForceScope(
                e.target.value as "india" | "us",
              )
            }
            className="rounded-md border border-amber-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-amber-700 dark:bg-gray-900 dark:text-gray-100"
          >
            <option value="india">India</option>
            <option value="us">US</option>
          </select>
          <button
            type="button"
            onClick={handleForceRefresh}
            disabled={forceBusy}
            className="rounded-md bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-60"
          >
            {forceBusy ? "Generating…" : "Generate"}
          </button>
          {forceMsg && (
            <span
              className={
                "text-[11px] " +
                (forceMsg.kind === "ok"
                  ? "text-emerald-700 dark:text-emerald-400"
                  : "text-red-700 dark:text-red-400")
              }
            >
              {forceMsg.text}
            </span>
          )}
        </div>
      </div>

      {filteredGroups.length === 0 && !loading ? (
        <div className="rounded-xl border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
          No recommendation runs match your filter.
        </div>
      ) : (
        <>
          <div className="space-y-2">
            {pagedGroups.map((g) => (
              <RunRow
                key={g.run_id}
                group={g}
                open={expanded.has(g.run_id)}
                onToggle={() => toggle(g.run_id)}
                onDeleteRun={setPendingDeleteRun}
                onPromoteRun={setPendingPromoteRun}
                onView={setViewRow}
                onDeleteRec={setPendingDeleteRec}
              />
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-2 text-xs text-gray-600 dark:text-gray-400">
            <div className="flex items-center gap-2">
              <span>{filteredGroups.length} runs</span>
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
                className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
              >
                {[10, 25, 50, 100].map((n) => (
                  <option key={n} value={n}>
                    {n}/page
                  </option>
                ))}
              </select>
              <DownloadCsvButton
                onClick={handleDownload}
                disabled={filteredGroups.length === 0}
                aria-label="Download CSV"
                title="Download CSV"
              />
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() =>
                  setPage((p) => Math.max(1, p - 1))
                }
                disabled={safePage <= 1}
                className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                Prev
              </button>
              <span className="tabular-nums">
                {safePage} / {maxPages}
              </span>
              <button
                type="button"
                onClick={() =>
                  setPage((p) =>
                    Math.min(maxPages, p + 1),
                  )
                }
                disabled={safePage >= maxPages}
                className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}

      <RationaleModal
        row={viewRow}
        onClose={() => setViewRow(null)}
      />

      <ConfirmDialog
        open={pendingDeleteRec !== null}
        title="Delete Recommendation"
        message={
          pendingDeleteRec
            ? `Delete recommendation for ${
                pendingDeleteRec.ticker ?? "(no ticker)"
              } owned by ${
                pendingDeleteRec.email ?? "unknown user"
              }? This also removes its outcome checkpoints and cannot be undone.`
            : ""
        }
        confirmLabel={busy ? "Deleting…" : "Delete"}
        variant="danger"
        onConfirm={confirmDeleteRec}
        onCancel={() => setPendingDeleteRec(null)}
      />

      <ConfirmDialog
        open={pendingDeleteRun !== null}
        title="Delete Whole Run"
        message={
          pendingDeleteRun
            ? `Delete this entire run with ${
                pendingDeleteRun.recs.length
              } recommendation${
                pendingDeleteRun.recs.length === 1
                  ? ""
                  : "s"
              } (owned by ${
                pendingDeleteRun.email ?? "unknown user"
              })? All child recommendations and their outcome checkpoints will be removed. This cannot be undone.`
            : ""
        }
        confirmLabel={
          busy ? "Deleting…" : "Delete run"
        }
        variant="danger"
        onConfirm={confirmDeleteRun}
        onCancel={() => setPendingDeleteRun(null)}
      />

      <ConfirmDialog
        open={pendingPromoteRun !== null}
        title="Replace with Test Run"
        message={
          pendingPromoteRun
            ? `Promote this TEST run (${pendingPromoteRun.recs.length} recommendation${
                pendingPromoteRun.recs.length === 1
                  ? ""
                  : "s"
              }) to the user's active run for ${
                pendingPromoteRun.scope
              }? Any existing non-test run this month will be permanently deleted (outcomes included). This cannot be undone.`
            : ""
        }
        confirmLabel={
          busy ? "Replacing…" : "Replace"
        }
        variant="danger"
        onConfirm={confirmPromoteRun}
        onCancel={() => setPendingPromoteRun(null)}
      />
    </div>
  );
}
