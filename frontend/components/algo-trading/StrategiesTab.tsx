"use client";
/**
 * Algo Trading — Strategies tab. Slice 4 ships the list view +
 * archive action; Slice 5 wires the visual builder via the
 * "New strategy" / "Edit" buttons.
 */

import { useCallback, useState } from "react";

import {
  archiveStrategy,
  useStrategies,
  type StrategySummary,
} from "@/hooks/useStrategies";

interface Props {
  onOpenBuilder?: (id: string | null) => void;  // null = new
}

export function StrategiesTab({ onOpenBuilder }: Props) {
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
          onClick={() => onOpenBuilder?.(null)}
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
                <Th>Name</Th>
                <Th>Mode</Th>
                <Th>Status</Th>
                <Th>Updated</Th>
                <Th align="right">Actions</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {strategies.map((s) => (
                <Row
                  key={s.id}
                  s={s}
                  busy={busyId === s.id}
                  onEdit={() => onOpenBuilder?.(s.id)}
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

function Th({
  children, align = "left",
}: { children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <th
      scope="col"
      className={`px-3 py-2 text-${align} text-xs font-medium text-gray-600 dark:text-gray-300`}
    >
      {children}
    </th>
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
