"use client";
/**
 * Algo Trading — Strategies tab. Two-mode container:
 *
 * - list: shows saved strategies + "+ New strategy" / "Edit"
 *   buttons that flip into builder mode.
 * - builder: full StrategyBuilder; "Save" or "Cancel" returns
 *   to list mode.
 */

import { useCallback, useState } from "react";
import useSWR from "swr";

import { StrategyBuilder } from "@/components/algo-trading/builder/StrategyBuilder";
import {
  archiveStrategy,
  useStrategies,
  type StrategyAst,
  type StrategySummary,
} from "@/hooks/useStrategies";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

type Mode =
  | { kind: "list" }
  | { kind: "builder"; id: string | null };

async function fetchAst(id: string): Promise<StrategyAst> {
  const r = await apiFetch(`${API_URL}/algo/strategies/${id}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
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
  const { strategies, loading, error } = useStrategies();
  const [busyId, setBusyId] = useState<string | null>(null);

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

      {loading && strategies.length === 0 ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : strategies.length === 0 ? (
        <p
          data-testid="algo-strategies-empty"
          className="text-sm text-gray-500 dark:text-gray-400"
        >
          No strategies yet. Click &ldquo;+ New strategy&rdquo; to draft one.
        </p>
      ) : (
        <div
          data-testid="algo-strategies-list"
          className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700"
        >
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800/50 text-xs">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Name</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Mode</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Status</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">Updated</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-600 dark:text-gray-300">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {strategies.map((s) => (
                <Row
                  key={s.id}
                  s={s}
                  busy={busyId === s.id}
                  onEdit={() => onOpenBuilder(s.id)}
                  onArchive={() => handleArchive(s.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Row({
  s, busy, onEdit, onArchive,
}: {
  s: StrategySummary;
  busy: boolean;
  onEdit: () => void;
  onArchive: () => void;
}) {
  return (
    <tr
      data-testid={`algo-strategies-row-${s.id}`}
      className="hover:bg-gray-50 dark:hover:bg-gray-800/50"
    >
      <td className="px-3 py-2">{s.name}</td>
      <td className="px-3 py-2 text-gray-500">{s.mode}</td>
      <td className="px-3 py-2 text-gray-500">{s.status}</td>
      <td className="px-3 py-2 text-gray-500">
        {s.updated_at ? new Date(s.updated_at).toLocaleString() : "—"}
      </td>
      <td className="px-3 py-2 text-right space-x-2">
        <button
          type="button"
          onClick={onEdit}
          data-testid={`algo-strategies-edit-${s.id}`}
          className="text-indigo-600 dark:text-indigo-400 hover:underline text-xs"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={onArchive}
          disabled={busy}
          data-testid={`algo-strategies-archive-${s.id}`}
          className="text-red-600 dark:text-red-400 hover:underline text-xs disabled:opacity-40"
        >
          {busy ? "Archiving…" : "Archive"}
        </button>
      </td>
    </tr>
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
      onSaved={onDone}
      onCancel={onDone}
    />
  );
}
