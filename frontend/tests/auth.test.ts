/**
 * Unit tests for frontend/lib/auth.ts JWT helpers.
 *
 * No network requests are made — all tokens are generated inline.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimal base64url-encoded JWT with the supplied payload.
 * This does NOT produce a valid cryptographic signature — it is only used
 * for client-side decoding tests.
 */
function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const body = btoa(JSON.stringify(payload))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `${header}.${body}.fakesig`;
}

function nowSec(): number {
  return Math.floor(Date.now() / 1000);
}

// ---------------------------------------------------------------------------
// isTokenExpired
// ---------------------------------------------------------------------------

describe("isTokenExpired", () => {
  // We import after mocking localStorage
  let isTokenExpired: (token: string | null) => boolean;

  beforeEach(async () => {
    vi.resetModules();
    // Provide a minimal localStorage shim
    const store: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v; },
      removeItem: (k: string) => { delete store[k]; },
    });
    const mod = await import("../lib/auth");
    isTokenExpired = mod.isTokenExpired;
  });

  it("returns true for null", () => {
    expect(isTokenExpired(null)).toBe(true);
  });

  it("returns true for empty string", () => {
    expect(isTokenExpired("")).toBe(true);
  });

  it("returns true for a malformed token", () => {
    expect(isTokenExpired("not.a.jwt")).toBe(true);
  });

  it("returns true for an expired token", () => {
    const token = makeJwt({ sub: "u1", exp: nowSec() - 120 });
    expect(isTokenExpired(token)).toBe(true);
  });

  it("returns false for a valid (future-expiry) token", () => {
    const token = makeJwt({ sub: "u1", exp: nowSec() + 3600 });
    expect(isTokenExpired(token)).toBe(false);
  });

  it("returns true when exp is exactly now (within 30s buffer)", () => {
    // exp = now means it's within the 30-second buffer → treated as expired
    const token = makeJwt({ sub: "u1", exp: nowSec() });
    expect(isTokenExpired(token)).toBe(true);
  });

  it("returns true when token has no exp claim", () => {
    const token = makeJwt({ sub: "u1" });
    expect(isTokenExpired(token)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// getRoleFromToken
// ---------------------------------------------------------------------------

describe("getRoleFromToken", () => {
  let getRoleFromToken: () => string | null;
  let setTokens: (a: string, r: string) => void;
  let clearTokens: () => void;
  let store: Record<string, string>;

  beforeEach(async () => {
    vi.resetModules();
    store = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v; },
      removeItem: (k: string) => { delete store[k]; },
    });
    const mod = await import("../lib/auth");
    getRoleFromToken = mod.getRoleFromToken;
    setTokens = mod.setTokens;
    clearTokens = mod.clearTokens;
  });

  it("returns null when no token is stored", () => {
    expect(getRoleFromToken()).toBeNull();
  });

  it("returns the role from a valid access token", () => {
    const token = makeJwt({ sub: "u1", role: "superuser", exp: nowSec() + 3600 });
    setTokens(token, "refresh-placeholder");
    expect(getRoleFromToken()).toBe("superuser");
  });

  it("returns null after tokens are cleared", () => {
    const token = makeJwt({ sub: "u1", role: "general", exp: nowSec() + 3600 });
    setTokens(token, "refresh-placeholder");
    clearTokens();
    expect(getRoleFromToken()).toBeNull();
  });

  it("returns null when the token payload has no role claim", () => {
    const token = makeJwt({ sub: "u1", exp: nowSec() + 3600 });
    setTokens(token, "refresh-placeholder");
    expect(getRoleFromToken()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// setTokens / getAccessToken / getRefreshToken / clearTokens
// ---------------------------------------------------------------------------

describe("token storage helpers", () => {
  let mod: typeof import("../lib/auth");
  let store: Record<string, string>;

  beforeEach(async () => {
    vi.resetModules();
    store = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v; },
      removeItem: (k: string) => { delete store[k]; },
    });
    mod = await import("../lib/auth");
  });

  it("setTokens persists access token only", () => {
    mod.setTokens("access-tok", "refresh-tok");
    expect(mod.getAccessToken()).toBe("access-tok");
    // Refresh token is now HttpOnly cookie — not in localStorage.
    expect(mod.getRefreshToken()).toBeNull();
  });

  it("clearTokens removes access token", () => {
    mod.setTokens("access-tok", "refresh-tok");
    mod.clearTokens();
    expect(mod.getAccessToken()).toBeNull();
    expect(mod.getRefreshToken()).toBeNull();
  });
});
