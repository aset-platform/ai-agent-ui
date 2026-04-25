/**
 * Dashboard route — Server Component wrapper
 * (ASETPLTFRM-334 phase A.4).
 *
 * Pre-fetches `/v1/dashboard/home` on the server using
 * the access_token cookie set during login (phase
 * A.1). The fetched payload is handed to the existing
 * client tree as `initialData`, which seeds SWR's
 * `fallbackData` so the first render paints with real
 * data — no skeleton step, no client-side waterfall.
 *
 * Why this is the LCP win:
 *
 * - Previously the dashboard hydrated client-side,
 *   then fetched /dashboard/home (~500–2000 ms cold,
 *   ~50 ms warm after phase D), then painted hero
 *   widgets. LCP was the first hero widget paint,
 *   ~4.7 s per the 334 audit baseline.
 * - Now the server resolves the API call before
 *   sending HTML; the hydrated tree's first render
 *   already has the data.
 *
 * Failure handling:
 *
 * - `serverApiOrNull` returns null on 401/403 — the
 *   client's existing SWR call will then fetch from
 *   scratch on hydrate. Stale-cookie users hit the
 *   slow path once, then the proxy bounces them to
 *   /login.
 * - On other errors (5xx, network), the catch falls
 *   through to `initialData=undefined` and the client
 *   degrades to the pre-A.4 skeleton path.
 */

import DashboardClient from "./DashboardClient";
import { serverApiOrNull } from "@/lib/serverApi";
import type { DashboardHomeResponse } from "@/lib/types";

export default async function DashboardPage() {
  let initialData: DashboardHomeResponse | undefined;
  try {
    const data = await serverApiOrNull<
      DashboardHomeResponse
    >("/dashboard/home");
    initialData = data ?? undefined;
  } catch {
    // Network/5xx — degrade to client-side fetch.
    initialData = undefined;
  }

  return <DashboardClient initialData={initialData} />;
}
