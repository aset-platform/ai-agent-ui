"use client";
/**
 * Triggers a browser CSV download from a backend endpoint.
 *
 * - Uses ``apiFetch`` so JWT auto-refresh works (§4.2 #14).
 * - Honours ``Content-Disposition`` filename if present.
 * - Cleans up the object URL after the click.
 */

import { apiFetch } from "@/lib/apiFetch";

const DEFAULT_FILENAME = "export.csv";

function filenameFromHeader(h: string | null): string {
  if (!h) return DEFAULT_FILENAME;
  const m = h.match(/filename="([^"]+)"/);
  return m ? m[1] : DEFAULT_FILENAME;
}

export async function triggerCsvDownload(url: string): Promise<void> {
  const res = await apiFetch(url);
  if (!res.ok) {
    throw new Error(`CSV export failed: HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = filenameFromHeader(
    res.headers.get("Content-Disposition"),
  );
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objUrl);
}
