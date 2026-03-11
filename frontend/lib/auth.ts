/**
 * JWT token helpers for client-side authentication.
 *
 * All functions are safe to call during SSR — they guard against
 * `localStorage` being unavailable (e.g. on the server) by checking
 * for `typeof window === "undefined"` before reading storage.
 *
 * Token storage keys
 * ------------------
 * - `auth_access_token`  — short-lived JWT (60 min)
 * - `auth_refresh_token` — long-lived JWT (7 days)
 */

// Re-export PKCE helpers so callers can import everything auth-related
// from a single module.
export {
  clearOAuthSession,
  generateCodeChallenge,
  generateCodeVerifier,
  getStoredProvider,
  getStoredVerifier,
  storeOAuthSession,
} from "@/lib/oauth";

import { BACKEND_URL } from "@/lib/config";

const ACCESS_KEY = "auth_access_token";

// ---------------------------------------------------------------------------
// Storage helpers
// ---------------------------------------------------------------------------

/** Return the stored access token, or null if unavailable. */
export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

/**
 * Return the stored refresh token.
 *
 * @deprecated Refresh tokens are now HttpOnly cookies managed by
 *   the server. This function returns null; kept for backward
 *   compatibility with callers that have not been updated yet.
 */
export function getRefreshToken(): string | null {
  return null;
}

/**
 * Persist tokens.
 *
 * Only the access token is stored in localStorage; the refresh
 * token is set as an HttpOnly cookie by the server.
 */
export function setTokens(access: string, _refresh?: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_KEY, access);
}

/** Remove the access token from localStorage. */
export function clearTokens(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_KEY);
}

// ---------------------------------------------------------------------------
// JWT decoding (client-side — no signature verification needed here,
// the server validates on every request)
// ---------------------------------------------------------------------------

interface JwtPayload {
  sub?: string;
  email?: string;
  role?: string;
  exp?: number;
  type?: string;
}

/**
 * Decode the payload of a JWT without verifying its signature.
 * Returns null if the token is malformed.
 */
function decodePayload(token: string): JwtPayload | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    // Base64url → Base64 → JSON
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(base64);
    return JSON.parse(json) as JwtPayload;
  } catch {
    return null;
  }
}

/**
 * Return true if the token is missing, malformed, or its `exp` claim is
 * in the past.  Adds a 30-second clock-skew buffer.
 */
export function isTokenExpired(token: string | null): boolean {
  if (!token) return true;
  const payload = decodePayload(token);
  if (!payload || typeof payload.exp !== "number") return true;
  // exp is seconds since epoch; add 30s buffer
  return Date.now() / 1000 >= payload.exp - 30;
}

/**
 * Extract the user's role from the stored access token.
 * Returns null if no valid token is present.
 */
export function getRoleFromToken(): string | null {
  const token = getAccessToken();
  if (!token) return null;
  return decodePayload(token)?.role ?? null;
}

/**
 * Extract the user ID (sub claim) from the stored access token.
 * Returns null if no valid token is present.
 */
export function getUserIdFromToken(): string | null {
  const token = getAccessToken();
  if (!token) return null;
  return decodePayload(token)?.sub ?? null;
}

// ---------------------------------------------------------------------------
// Token refresh
// ---------------------------------------------------------------------------

/**
 * Exchange the stored refresh token for a new access + refresh token pair.
 *
 * On success, updates localStorage and returns the new access token.
 * On failure, clears all tokens and returns null (caller should redirect
 * to `/login`).
 */
export async function refreshAccessToken(): Promise<string | null> {
  // Fix #14: 10-second timeout prevents a hung refresh from blocking all API calls
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);

  // Fix #14: 10-second timeout prevents a hung refresh from blocking all API calls
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);

  // Fix #14: 10-second timeout prevents a hung refresh from blocking all API calls
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);

  // Fix #14: 10-second timeout prevents a hung refresh from blocking all API calls
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);

  try {
    // The refresh token is sent automatically via HttpOnly cookie.
    const res = await fetch(`${BACKEND_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({}),
      signal: controller.signal,
    });

    if (!res.ok) {
      clearTokens();
      return null;
    }

    const data = (await res.json()) as {
      access_token: string;
      refresh_token: string;
    };
    setTokens(data.access_token);
    return data.access_token;
  } catch {
    clearTokens();
    return null;
  } finally {
    clearTimeout(timer);
  }
}
