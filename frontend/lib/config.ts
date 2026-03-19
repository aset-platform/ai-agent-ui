/**
 * Centralized configuration for service URLs.
 *
 * Environment variables take precedence; fallback to localhost defaults.
 */

/** Backend host URL (no trailing slash). Used for static assets and WS derivation. */
export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8181";

/** Versioned API base URL (no trailing slash). All API calls go through /v1. */
export const API_URL = `${BACKEND_URL}/v1`;

/** WebSocket URL derived from BACKEND_URL (http→ws, https→wss). */
export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ??
  BACKEND_URL.replace(/^http/, "ws");

/** Docs base URL (no trailing slash). */
export const DOCS_URL =
  process.env.NEXT_PUBLIC_DOCS_URL ?? "http://127.0.0.1:8000";
