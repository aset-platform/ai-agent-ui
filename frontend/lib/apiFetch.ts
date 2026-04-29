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

import { API_URL } from "@/lib/config";
import {
  clearTokens,
  getAccessToken,
  isTokenExpired,
  refreshAccessToken,
} from "@/lib/auth";

/**
 * Best-effort cookie wipe + local cleanup + redirect to /login.
 *
 * The HttpOnly `access_token` / `refresh_token` cookies are NOT
 * accessible from JS, so `clearTokens()` (localStorage only) leaves
 * proxy.ts seeing them and bouncing /login → /dashboard. We POST
 * /v1/auth/logout (auth-tolerant on the backend) to get the server
 * to send Set-Cookie clears, then clear localStorage, then navigate.
 *
 * See CLAUDE.md §5.3 / §6.6 — this mirrors `AppHeader.handleSignOut`.
 */
async function bounceToLogin(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    await fetch(`${API_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
  } catch {
    // Best effort — proceed with local cleanup either way.
  }
  clearTokens();
  window.location.href = "/login";
}

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
      // Refresh failed — clear server cookies AND local
      // token, then bounce to /login (otherwise proxy.ts
      // sees the lingering refresh_token cookie and
      // redirects right back to /dashboard).
      void bounceToLogin();
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

  // The server rejected our token — clear cookies AND
  // localStorage, then redirect (see bounceToLogin).
  if (response.status === 401) {
    void bounceToLogin();
  }

  return response;
}
