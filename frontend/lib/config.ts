/**
 * Centralized configuration for service URLs.
 *
 * Environment variables take precedence; fallback to localhost defaults.
 */

/** Backend API base URL (no trailing slash). */
export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8181";

/** Dashboard base URL (no trailing slash). */
export const DASHBOARD_URL =
  process.env.NEXT_PUBLIC_DASHBOARD_URL ?? "http://127.0.0.1:8050";

/** Docs base URL (no trailing slash). */
export const DOCS_URL =
  process.env.NEXT_PUBLIC_DOCS_URL ?? "http://127.0.0.1:8000";
