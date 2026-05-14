"use client";
/**
 * Algo Trading — Strategies tab. Two-mode container:
 *
 * - list: shows saved strategies + "+ New strategy" / "Edit"
 *   buttons that flip into builder mode.
 * - builder: full StrategyBuilder; "Save" or "Cancel" returns
 *   to list mode.
 *
 * The list view follows the tabular pattern from CLAUDE.md §5.4:
 * search input + status filter + paginated table footer.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PromoteModal } from "@/components/algo-trading/PromoteModal";
import { StrategyBuilder } from "@/components/algo-trading/builder/StrategyBuilder";
import {
  archiveStrategy,
  cloneStrategy,
  useStrategies,
  type StrategyAst,
  type StrategyMode,
  type StrategySummary,
} from "@/hooks/useStrategies";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

type Mode =
  | { kind: "list" }
  | { kind: "builder"; id: string | null };

type StatusFilter = "active" | "archived" | "all";

const PAGE_SIZES = [10, 25, 50] as const;

async function fetchAst(id: string): Promise<StrategyAst> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  // REGIME-3: GET response is now wrapped — { strategy,
  // applicable_regimes }.  StrategiesTab only renders the AST so
  // it pulls .strategy out of the wrapper here.  Tolerates the
  // legacy bare-AST shape during rolling deploys.
  const body = (await r.json()) as
    | StrategyAst
    | { strategy: StrategyAst; applicable_regimes?: string[] };
  if (
    typeof body === "object" &&
    body !== null &&
    "strategy" in body &&
    body.strategy
  ) {
    return body.strategy;
  }
  return body as StrategyAst;
}

export function StrategiesTab() {
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  if (mode.kind === "builder") {
    return (
      <BuilderMode
        id={mode.id}
        onDone={() => setMode({ kind: "list" })}
      />
    );
  }
  return <ListMode onOpenBuilder={(id) => setMode({ kind: "builder", id })} />;
}

function ListMode({
  onOpenBuilder,
}: { onOpenBuilder: (id: string | null) => void }) {
  // Fetch with archived rows included; the status select picks
  // which slice to show. The 'active'-only cache used by the other
  // selectors (BacktestRunForm, ActiveRunsPanel, ...) stays
  // untouched.
  const { strategies, loading, error } = useStrategies({
    includeArchived: true,
  });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZES[1]);
  const [confirmTarget, setConfirmTarget] =
    useState<StrategySummary | null>(null);
  const [promoteTarget, setPromoteTarget] =
    useState<StrategySummary | null>(null);

  const handleArchive = useCallback(async (id: string) => {
    setBusyId(id);
    try {
      await archiveStrategy(id);
    } catch (err) {
      console.error("Archive failed", err);
    } finally {
      setBusyId(null);
    }
  }, []);

  const handleConfirmArchive = useCallback(async () => {
    if (!confirmTarget) return;
    const target = confirmTarget;
    setConfirmTarget(null);
    await handleArchive(target.id);
  }, [confirmTarget, handleArchive]);

  const handleClone = useCallback(
    async (id: string) => {
      setBusyId(id);
      try {
        const newId = await cloneStrategy(id);
        onOpenBuilder(newId);
      } catch (err) {
        console.error("Clone failed", err);
      } finally {
        setBusyId(null);
      }
    },
    [onOpenBuilder],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return strategies.filter((s) => {
      const isArchived = s.archived_at !== null;
      if (status === "active" && isArchived) return false;
      if (status === "archived" && !isArchived) return false;
      if (q && !s.name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [strategies, query, status]);

  const maxPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  // Reset to page 1 whenever filters narrow the result set below
  // the current page index. ``maxPages`` already reflects the new
  // filter so this keeps Prev/Next coherent.
  useEffect(() => {
    if (page > maxPages) setPage(1);
  }, [page, maxPages]);

  const pageRows = useMemo(
    () => filtered.slice((page - 1) * pageSize, page * pageSize),
    [filtered, page, pageSize],
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Strategies
        </h2>
        <button
          type="button"
          onClick={() => onOpenBuilder(null)}
          data-testid="algo-strategies-new"
          className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1 text-xs"
        >
          + New strategy
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-xs p-2"
        >
          {error}
        </div>
      )}

      <WorkflowCallout />


      <div className="flex items-center justify-end gap-2">
        <label className="relative">
          <span className="sr-only">Search strategies</span>
          <SearchIcon className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-gray-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
            placeholder="Search…"
            data-testid="algo-strategies-search"
            className="w-44 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 pl-6 pr-2 py-1 text-xs"
          />
        </label>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as StatusFilter);
            setPage(1);
          }}
          data-testid="algo-strategies-status-filter"
          className="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-xs"
        >
          <option value="active">Active</option>
          <option value="archived">Archived</option>
          <option value="all">All</option>
        </select>
      </div>

      {loading && strategies.length === 0 ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : filtered.length === 0 ? (
        <p
          data-testid="algo-strategies-empty"
          className="text-sm text-gray-500 dark:text-gray-400"
        >
          {strategies.length === 0
            ? "No strategies yet. Click “+ New strategy” to draft one."
            : "No strategies match the current filters."}
        </p>
      ) : (
        <>
          <div
            data-testid="algo-strategies-list"
            className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700"
          >
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 dark:bg-gray-800/50 text-xs">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Name</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Mode</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Runtime</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Updated</th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-gray-600 dark:text-gray-300">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                {pageRows.map((s) => (
                  <Row
                    key={s.id}
                    s={s}
                    busy={busyId === s.id}
                    onEdit={() => onOpenBuilder(s.id)}
                    onClone={() => handleClone(s.id)}
                    onArchive={() => setConfirmTarget(s)}
                    onPromote={() => setPromoteTarget(s)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
            <div className="flex items-center gap-2">
              <span>
                {filtered.length} row{filtered.length !== 1 ? "s" : ""}
              </span>
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
                data-testid="algo-strategies-page-size"
                className="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-1.5 py-0.5 text-xs"
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n}/page
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                data-testid="algo-strategies-prev"
                className="rounded px-2 py-1 hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Prev
              </button>
              <span data-testid="algo-strategies-page-indicator">
                {page} / {maxPages}
              </span>
              <button
                type="button"
                onClick={() =>
                  setPage((p) => Math.min(maxPages, p + 1))
                }
                disabled={page >= maxPages}
                data-testid="algo-strategies-next"
                className="rounded px-2 py-1 hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}

      <ConfirmDialog
        open={confirmTarget !== null}
        title={
          confirmTarget?.archived_at
            ? "Delete strategy permanently"
            : "Archive strategy"
        }
        message={
          confirmTarget?.archived_at
            ? `“${confirmTarget?.name ?? ""}” is already archived. Deleting permanently cannot be undone.`
            : `“${confirmTarget?.name ?? ""}” will be archived. You can still see it under the Archived filter and can delete it permanently from there.`
        }
        confirmLabel={
          confirmTarget?.archived_at ? "Delete" : "Archive"
        }
        variant={confirmTarget?.archived_at ? "danger" : "warning"}
        onCancel={() => setConfirmTarget(null)}
        onConfirm={handleConfirmArchive}
      />

      {promoteTarget && (
        <PromoteModal
          strategy={promoteTarget}
          open
          onClose={() => setPromoteTarget(null)}
          onPromoted={() => setPromoteTarget(null)}
        />
      )}
    </div>
  );
}

function Row({
  s, busy, onEdit, onClone, onArchive, onPromote,
}: {
  s: StrategySummary;
  busy: boolean;
  onEdit: () => void;
  onClone: () => void;
  onArchive: () => void;
  onPromote: () => void;
}) {
  const isArchived = s.archived_at !== null;
  const mode = (s.mode as StrategyMode) ?? "draft";
  const canPromote = mode !== "live" && !isArchived;
  // History tooltip: who promoted last + when. Hover-only so the
  // row stays clean for the 90% case where the user doesn't care.
  const historyTitle =
    s.last_transition_at && s.last_transition_by
      ? `Last transition by ${s.last_transition_by} · ${new Date(
          s.last_transition_at,
        ).toLocaleString()}`
      : undefined;
  return (
    <tr
      data-testid={`algo-strategies-row-${s.id}`}
      className="hover:bg-gray-50 dark:hover:bg-gray-800/50"
    >
      <td className="px-3 py-2">
        <div className="font-medium text-gray-900 dark:text-gray-100">
          {s.name}
        </div>
        {s.updated_at && (
          <div className="text-[10px] text-gray-400 dark:text-gray-500">
            Updated {new Date(s.updated_at).toLocaleDateString()}
          </div>
        )}
      </td>
      <td className="px-3 py-2">
        <ModeBadge mode={mode} title={historyTitle} />
      </td>
      <td className="px-3 py-2 text-xs text-gray-500">
        {isArchived ? (
          <span className="text-gray-400">archived</span>
        ) : s.has_active_runtime ? (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            {s.active_runtime_modes?.join(" + ") ?? "running"}
          </span>
        ) : (
          <span className="text-gray-400">idle</span>
        )}
        {s.open_position_count != null &&
          s.open_position_count > 0 && (
            <div className="text-[10px] text-rose-600 dark:text-rose-400">
              {s.open_position_count} open pos.
            </div>
          )}
      </td>
      <td className="px-3 py-2 text-gray-500 text-xs">
        {s.updated_at ? new Date(s.updated_at).toLocaleString() : "—"}
      </td>
      <td className="px-3 py-2 text-right">
        <div className="inline-flex items-center gap-1">
          <IconButton
            label="Promote"
            onClick={onPromote}
            disabled={
              busy ||
              isArchived ||
              (!canPromote && !s.has_ever_been_live)
            }
            data-testid={`algo-strategies-promote-${s.id}`}
            tone="indigo"
          >
            <ArrowUpIcon />
          </IconButton>
          <IconButton
            label="Edit"
            onClick={onEdit}
            disabled={busy || isArchived}
            data-testid={`algo-strategies-edit-${s.id}`}
            tone="indigo"
          >
            <PencilIcon />
          </IconButton>
          <IconButton
            label="Clone"
            onClick={onClone}
            disabled={busy}
            data-testid={`algo-strategies-clone-${s.id}`}
            tone="indigo"
          >
            <DuplicateIcon />
          </IconButton>
          <IconButton
            label={isArchived ? "Delete" : "Archive"}
            onClick={onArchive}
            disabled={busy}
            data-testid={`algo-strategies-archive-${s.id}`}
            tone="red"
          >
            <TrashIcon />
          </IconButton>
        </div>
      </td>
    </tr>
  );
}

function ModeBadge({
  mode, title,
}: { mode: StrategyMode; title?: string }) {
  const styles: Record<StrategyMode, string> = {
    draft:
      "bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600",
    paper:
      "bg-sky-100 text-sky-800 border-sky-300 dark:bg-sky-900/30 dark:text-sky-300 dark:border-sky-700",
    live:
      "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-700",
  };
  const label =
    mode.charAt(0).toUpperCase() + mode.slice(1);
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${styles[mode]}`}
      title={title}
      data-testid={`algo-strategies-mode-badge-${mode}`}
    >
      {label}
    </span>
  );
}

function WorkflowCallout() {
  return (
    <div
      data-testid="algo-strategies-workflow-callout"
      className="rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3 text-xs text-slate-600 dark:text-slate-300"
    >
      <div className="font-medium text-slate-800 dark:text-slate-200 mb-1">
        Strategy promotion workflow
      </div>
      <ul className="list-disc pl-4 space-y-0.5">
        <li>
          <span className="font-medium">Draft → Paper</span>:
          needs a completed backtest + walk-forward run.
        </li>
        <li>
          <span className="font-medium">Paper → Live</span>: needs
          a completed paper run.
        </li>
        <li>
          Pickers in Paper / Dry-run / Live tabs only show
          strategies graduated to that stage.
        </li>
        <li>
          Editing a Paper or Live strategy auto-demotes it back to
          Draft (active runtimes keep running the cached AST).
        </li>
        <li>
          Strategies that have been Live once can be re-promoted
          directly to Live via the bypass workflow.
        </li>
      </ul>
    </div>
  );
}

function ArrowUpIcon({ className = "h-3.5 w-3.5" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="12" y1="19" x2="12" y2="5" />
      <polyline points="5 12 12 5 19 12" />
    </svg>
  );
}

function IconButton({
  label,
  tone,
  children,
  ...rest
}: {
  label: string;
  tone: "indigo" | "red";
  children: React.ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const colour =
    tone === "indigo"
      ? "text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/30"
      : "text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30";
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`rounded p-1 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${colour}`}
      {...rest}
    >
      {children}
    </button>
  );
}

function SearchIcon({ className = "h-3.5 w-3.5" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

function PencilIcon({ className = "h-3.5 w-3.5" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  );
}

function DuplicateIcon({ className = "h-3.5 w-3.5" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function TrashIcon({ className = "h-3.5 w-3.5" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function BuilderMode({
  id, onDone,
}: { id: string | null; onDone: () => void }) {
  const { data, error, isLoading } = useSWR<StrategyAst>(
    id ? `${API_URL}/algo/strategies/${id}` : null,
    () => (id ? fetchAst(id) : Promise.reject()),
    { revalidateOnFocus: false },
  );
  // The list endpoint already carries mode + runtime info on every
  // row; reuse the SWR cache instead of a new HTTP call so the
  // builder's demote-warning banner stays in sync with the list.
  const { strategies } = useStrategies({ includeArchived: true });
  const summary = id ? strategies.find((s) => s.id === id) : null;

  if (id && isLoading) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }
  if (error) {
    return (
      <div role="alert" className="text-xs text-red-600 dark:text-red-400">
        {(error as Error).message}
      </div>
    );
  }

  return (
    <StrategyBuilder
      strategyId={id}
      initial={data ?? null}
      currentMode={
        (summary?.mode as StrategyMode | undefined) ?? null
      }
      activeRuntimeModes={summary?.active_runtime_modes ?? []}
      openPositionCount={summary?.open_position_count ?? 0}
      onSaved={onDone}
      onCancel={onDone}
    />
  );
}
