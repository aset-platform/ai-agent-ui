/**
 * Server-side fetch helper for React Server
 * Components (ASETPLTFRM-334 phase A.3).
 *
 * `apiFetch` reads the access token from
 * `localStorage` — that doesn't exist on the
 * server. RSC code paths instead pull the token
 * from the HttpOnly `access_token` cookie set
 * during login (phase A.1) via `next/headers`.
 *
 * Use cases:
 *
 *   ```tsx
 *   // app/(authenticated)/dashboard/page.tsx
 *   import { serverApi } from "@/lib/serverApi";
 *
 *   export default async function DashboardPage() {
 *     const home = await serverApi<DashboardHome>(
 *       "/dashboard/home",
 *     );
 *     return <HeroSection data={home} />;
 *   }
 *   ```
 *
 * Errors:
 *
 * - Throws on 401/403 — the proxy should have
 *   already redirected unauth'd requests, so a 401
 *   here means a stale cookie that survived the
 *   presence check. Caller usually wraps in
 *   try/catch and renders a fallback.
 * - Throws on non-2xx with status + body for
 *   debuggability.
 *
 * Caching:
 *
 * - Defaults to `cache: "no-store"` so RSC always
 *   sees the freshest data. Override per-call via
 *   `init.cache` or `init.next.revalidate` when
 *   the data is safe to cache between requests.
 */

import { cookies } from "next/headers";

const ACCESS_COOKIE = "access_token";

/**
 * Backend URL resolution for server-side fetches.
 *
 * Server components cannot rely on the same-origin
 * `/v1/*` rewrite that browser code uses — there is
 * no Next.js dev server on the server side. We need
 * an absolute URL.
 *
 * Priority:
 *   1. `BACKEND_URL` env (set by docker-compose for
 *      container-to-container calls, e.g.
 *      `http://backend:8181`).
 *   2. `NEXT_PUBLIC_BACKEND_URL` env (browser-facing
 *      URL — works as a fallback in dev when both
 *      services run on the host).
 *   3. `http://localhost:8181` dev default.
 */
function resolveBackendBase(): string {
  return (
    process.env.BACKEND_URL
    ?? process.env.NEXT_PUBLIC_BACKEND_URL
    ?? "http://localhost:8181"
  );
}

export class ServerApiError extends Error {
  status: number;
  body: string;

  constructor(
    status: number, body: string, message: string,
  ) {
    super(message);
    this.name = "ServerApiError";
    this.status = status;
    this.body = body;
  }
}

/**
 * Authenticated server-side fetch. Reads the access
 * token from the request's `access_token` cookie and
 * forwards it as a Bearer header to the backend.
 *
 * @param path  Path under `/v1` — e.g. `/dashboard/home`.
 *              Leading slash optional.
 * @param init  Standard `RequestInit`. `cache` defaults
 *              to `"no-store"` for fresh-on-every-render
 *              behaviour.
 * @returns     Parsed JSON, typed as `T`.
 * @throws      `ServerApiError` on non-2xx response.
 */
export async function serverApi<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const cookieStore = await cookies();
  const token = cookieStore.get(ACCESS_COOKIE)?.value;
  const base = resolveBackendBase();
  const normalisedPath = path.startsWith("/")
    ? path
    : `/${path}`;
  const url = `${base}/v1${normalisedPath}`;

  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (
    !headers.has("content-type")
    && init.body != null
  ) {
    headers.set("content-type", "application/json");
  }

  const res = await fetch(url, {
    cache: "no-store",
    ...init,
    headers,
  });

  if (!res.ok) {
    const body = await res.text();
    throw new ServerApiError(
      res.status,
      body,
      `serverApi ${normalisedPath} failed: ${res.status}`,
    );
  }

  return res.json() as Promise<T>;
}

/**
 * Variant that returns null on 401/403 instead of
 * throwing. Useful when the RSC should degrade
 * gracefully (render an empty hero section) rather
 * than 500 the whole page.
 */
export async function serverApiOrNull<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T | null> {
  try {
    return await serverApi<T>(path, init);
  } catch (err) {
    if (
      err instanceof ServerApiError
      && (err.status === 401 || err.status === 403)
    ) {
      return null;
    }
    throw err;
  }
}
