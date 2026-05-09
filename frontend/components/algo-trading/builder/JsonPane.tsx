"use client";
/**
 * Live JSON preview of the strategy AST. Read-only by default
 * with a "Paste JSON" toggle that lets power users import a
 * full AST verbatim — server still re-validates on save.
 */

import { useState } from "react";

interface Props {
  ast: unknown;
  onPaste?: (raw: string) => { ok: boolean; error?: string };
}

export function JsonPane({ ast, onPaste }: Props) {
  const [editMode, setEditMode] = useState(false);
  const [draft, setDraft] = useState("");
  const [pasteError, setPasteError] = useState<string | null>(null);

  return (
    <div className="space-y-2 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-gray-700 dark:text-gray-200">
          JSON
        </span>
        {onPaste && (
          <button
            type="button"
            onClick={() => {
              setEditMode((m) => !m);
              setDraft(JSON.stringify(ast, null, 2));
              setPasteError(null);
            }}
            data-testid="algo-builder-json-toggle"
            className="text-indigo-600 dark:text-indigo-400 hover:underline"
          >
            {editMode ? "Cancel" : "Paste JSON"}
          </button>
        )}
      </div>
      {editMode ? (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            data-testid="algo-builder-json-input"
            className="w-full h-72 font-mono text-[11px] p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (!onPaste) return;
                const res = onPaste(draft);
                if (res.ok) {
                  setEditMode(false);
                  setPasteError(null);
                } else {
                  setPasteError(res.error ?? "Invalid JSON");
                }
              }}
              data-testid="algo-builder-json-apply"
              className="rounded bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1"
            >
              Apply
            </button>
          </div>
          {pasteError && (
            <div role="alert" className="text-red-600 dark:text-red-400">
              {pasteError}
            </div>
          )}
        </>
      ) : (
        <pre
          data-testid="algo-builder-json"
          className="font-mono text-[11px] p-2 rounded border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40 overflow-x-auto max-h-72"
        >
          {JSON.stringify(ast, null, 2)}
        </pre>
      )}
    </div>
  );
}
