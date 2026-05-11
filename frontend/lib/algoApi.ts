import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

/**
 * Trigger the kill-switch panic-close-all endpoint.
 *
 * POSTs to `/algo/kill-switch/panic-close-all` which submits
 * market-close orders for every algo-opened position via Kite.
 * Throws on non-2xx so callers (PanicCloseButton) can surface
 * the failure in-modal.
 */
export async function panicCloseAll(): Promise<void> {
  const r = await apiFetch(
    `${API_URL}/algo/kill-switch/panic-close-all`,
    { method: "POST" },
  );
  if (!r.ok) {
    throw new Error(`panic-close-all HTTP ${r.status}`);
  }
}
