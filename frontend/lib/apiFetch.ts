/**
 * Authenticated fetch wrapper.
 *
 * Behaves exactly like the native `fetch` API but:
 *
 * 1. Injects `Authorization: Bearer <token>` on every request.
 * 2. If the stored access token is expired, silently refreshes it before
 *    sending the request.
 * 3. On a `401` response from the server, clears all tokens and redirects
 *    the user to `/login`.
 *
 * Usage — replace bare `fetch(url, opts)` with `apiFetch(url, opts)`:
 *
 * ```typescript
 * import { apiFetch } from "@/lib/apiFetch";
 *
 * const res = await apiFetch("/chat/stream", {
 *   method: "POST",
 *   headers: { "Content-Type": "application/json" },
 *   body: JSON.stringify({ message: "Hello" }),
 * });
 * ```
 *
 * For streaming responses the caller receives the raw `Response` object and
 * can consume `res.body` as usual.
 */

import {
  clearTokens,
  getAccessToken,
  isTokenExpired,
  refreshAccessToken,
} from "@/lib/auth";

/**
 * Perform an authenticated HTTP request.
 *
 * @param input  - URL string or `Request` object (same as native `fetch`).
 * @param init   - Optional `RequestInit` options (method, headers, body…).
 * @returns      The `Response` object.  A `401` from the server causes a
 *               redirect to `/login` and the promise resolves with that
 *               `Response` (the component won't have time to use it).
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {}
): Promise<Response> {
  // Resolve the current access token, refreshing if necessary.
  let token = getAccessToken();

  if (isTokenExpired(token)) {
    token = await refreshAccessToken();
    if (!token) {
      // Refresh failed — send the user to login.
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      // Return a synthetic 401 so callers that await the result don't hang.
      return new Response(JSON.stringify({ detail: "Unauthorized" }), {
        status: 401,
      });
    }
  }

  // Merge the Authorization header into the caller-supplied headers.
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(input, {
    ...init,
    headers,
    credentials: "include",
  });

  // The server rejected our token — clear storage and redirect.
  if (response.status === 401) {
    clearTokens();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  }

  return response;
}
