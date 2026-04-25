/**
 * Centralized configuration for service URLs.
 *
 * Environment variables take precedence; fallback to localhost defaults.
 */

// Empty string opts into the same-origin `/v1/*` rewrite defined in
// `next.config.ts` (used by the `perf` compose profile to eliminate
// CORS against the baked origin). Any other value — including
// `undefined` — keeps the legacy absolute-URL behaviour.
const rawBackend = process.env.NEXT_PUBLIC_BACKEND_URL;
const useProxy = rawBackend === "";

/** Backend host URL (no trailing slash). Used for static assets and WS derivation. */
export const BACKEND_URL = useProxy
  ? ""
  : rawBackend ?? "http://127.0.0.1:8181";

/** Versioned API base URL (no trailing slash). All API calls go through /v1. */
export const API_URL = useProxy ? "/v1" : `${BACKEND_URL}/v1`;

/** WebSocket URL derived from BACKEND_URL (http→ws, https→wss). */
export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ??
  (useProxy
    ? "ws://localhost:8181"
    : BACKEND_URL.replace(/^http/, "ws"));

/** Docs base URL (no trailing slash). */
export const DOCS_URL =
  process.env.NEXT_PUBLIC_DOCS_URL ?? "http://localhost:8000";
