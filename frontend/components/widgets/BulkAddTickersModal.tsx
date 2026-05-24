"use client";

import { useRef, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  BulkTickerErrorRow,
  BulkTickerResponse,
} from "@/lib/types/bulkTickers";

interface Props {
  onClose: () => void;
  onUploaded: () => void; // parent SWR mutate()
}

const MAX_VISIBLE_ERRORS = 100;

export function BulkAddTickersModal(
  { onClose, onUploaded }: Props,
) {
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<
    BulkTickerResponse | null
  >(null);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleUpload() {
    if (!file) return;
    setSubmitting(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await apiFetch(
        `${API_URL}/users/me/tickers/bulk`,
        { method: "POST", body: fd },
      );
      if (!r.ok) {
        const body = await r.text();
        setErr(`Upload failed: ${r.status} ${body}`);
        return;
      }
      const data = (await r.json()) as BulkTickerResponse;
      setResult(data);
      onUploaded();
    } catch (exc) {
      setErr(
        exc instanceof Error
          ? `Upload failed: ${exc.message}`
          : "Upload failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const visibleErrors: BulkTickerErrorRow[] =
    result?.errors.slice(0, MAX_VISIBLE_ERRORS) ?? [];
  const truncatedCount =
    result && result.errors.length > MAX_VISIBLE_ERRORS
      ? result.errors.length - MAX_VISIBLE_ERRORS
      : 0;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="bulk-add-tickers-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-[520px] max-w-[95vw] space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">
            Bulk add tickers from CSV
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-xs underline"
          >
            Close
          </button>
        </div>

        {result === null ? (
          <>
            <p className="text-xs text-slate-500">
              Format: CSV with a <code>ticker</code> column.
              Up to 5,000 rows.
            </p>
            <label
              className="block rounded border border-dashed border-slate-300 dark:border-slate-600 px-4 py-6 text-center text-xs cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <input
                ref={inputRef}
                type="file"
                accept=".csv"
                className="hidden"
                data-testid="bulk-add-tickers-file-input"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setFile(f);
                }}
              />
              {file
                ? `Selected: ${file.name} (${(file.size / 1024).toFixed(0)} KB)`
                : "Drop .csv file here, or click to browse"}
            </label>
            {err && (
              <p className="text-xs text-rose-600">{err}</p>
            )}
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded border px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleUpload}
                disabled={!file || submitting}
                data-testid="bulk-add-tickers-upload-button"
                className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm disabled:opacity-50"
              >
                {submitting ? "Uploading…" : "Upload"}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="space-y-1 text-xs">
              <p
                data-testid="bulk-add-tickers-result-added-count"
                className="text-emerald-700 dark:text-emerald-400 font-medium"
              >
                {result.added.length} ticker
                {result.added.length === 1 ? "" : "s"} added
              </p>
              <p className="text-slate-500">
                {result.skipped_already_linked.length}{" "}
                already in your watchlist
              </p>
              <p className="text-rose-600">
                {result.errors.length} error
                {result.errors.length === 1 ? "" : "s"}
              </p>
            </div>
            {visibleErrors.length > 0 && (
              <div
                className="rounded border border-rose-200 dark:border-rose-800 max-h-48 overflow-y-auto p-2 text-[11px] font-mono"
                data-testid="bulk-add-tickers-result-errors-list"
              >
                {visibleErrors.map((e, i) => (
                  <div key={`${e.row}-${i}`}>
                    Row {e.row} · {e.ticker || "—"} ·{" "}
                    {e.reason}
                  </div>
                ))}
                {truncatedCount > 0 && (
                  <div className="text-slate-500 mt-1">
                    … {truncatedCount} more
                  </div>
                )}
              </div>
            )}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm"
              >
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
