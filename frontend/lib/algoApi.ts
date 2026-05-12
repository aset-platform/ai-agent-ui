import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

interface PanicCloseResult {
  tickers_closed: string[];
  orders_submitted: number;
  errors: string[];
  note?: string;
}

/**
 * Trigger the kill-switch panic-close-all endpoint.
 *
 * POSTs to `/algo/kill-switch/panic-close-all` which submits
 * market-close orders for every algo-opened position via Kite.
 *
 * The endpoint returns HTTP 200 even when individual Kite SELL
 * orders fail (CDSL TPIN unauthorised, margin block, freeze qty,
 * etc.) — the failures land in the response's `errors[]` array.
 * We translate any non-empty `errors[]` into a thrown Error so
 * PanicCloseButton's existing modal-error path can surface them
 * to the trader. Otherwise the user clicks "Close all", sees the
 * modal close, and incorrectly assumes everything sold.
 *
 * Throws on:
 *   - non-2xx HTTP status
 *   - any entries in `errors[]` (even if some orders did go
 *     through — partial failure still warrants visibility)
 *   - `orders_submitted == 0 && tickers_closed.length == 0` with
 *     the backend's note (e.g. "No algo-opened positions found")
 */
export async function panicCloseAll(): Promise<PanicCloseResult> {
  const r = await apiFetch(
    `${API_URL}/algo/kill-switch/panic-close-all`,
    { method: "POST" },
  );
  if (!r.ok) {
    let detail = "";
    try {
      const body = await r.json();
      detail = body?.detail ?? "";
    } catch {
      // ignore — fall through to bare status code
    }
    throw new Error(
      `panic-close-all HTTP ${r.status}`
      + (detail ? ` — ${detail}` : ""),
    );
  }
  const body = (await r.json()) as PanicCloseResult;
  if ((body.errors?.length ?? 0) > 0) {
    // Surface Kite's actual reason(s) — CDSL TPIN auth, margin,
    // freeze qty, etc. Join multiple errors with `;` so the
    // modal shows all of them when several tickers failed.
    throw new Error(body.errors.join("; "));
  }
  if (
    body.orders_submitted === 0
    && (body.tickers_closed?.length ?? 0) === 0
  ) {
    throw new Error(
      body.note || "No algo-opened positions to close.",
    );
  }
  return body;
}
