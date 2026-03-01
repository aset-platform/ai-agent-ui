/**
 * Unit tests for frontend/lib/apiFetch.ts.
 *
 * fetch() is mocked via vi.stubGlobal so no real network requests occur.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

type FetchMock = ReturnType<typeof vi.fn>;

async function setupApiFetch(opts: {
  accessToken?: string | null;
  refreshToken?: string | null;
  fetchImpl: FetchMock;
}) {
  vi.resetModules();

  const store: Record<string, string> = {};
  if (opts.accessToken) store["auth_access_token"] = opts.accessToken;
  if (opts.refreshToken) store["auth_refresh_token"] = opts.refreshToken;

  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
  });

  vi.stubGlobal("fetch", opts.fetchImpl);
  vi.stubGlobal("window", { location: { href: "" } });

  const { apiFetch } = await import("../lib/apiFetch");
  return { apiFetch, store };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("apiFetch", () => {
  it("sends Authorization header with valid access token", async () => {
    const token = makeJwt({ sub: "u1", exp: nowSec() + 3600 });
    const mockFetch = vi.fn().mockResolvedValue(makeResponse({ ok: true }));

    const { apiFetch } = await setupApiFetch({
      accessToken: token,
      fetchImpl: mockFetch,
    });

    await apiFetch("http://localhost:8181/chat");

    const [, init] = mockFetch.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${token}`);
  });

  it("refreshes an expired access token before the request", async () => {
    const expiredToken = makeJwt({ sub: "u1", exp: nowSec() - 60 });
    const newAccessToken = makeJwt({ sub: "u1", exp: nowSec() + 3600 });
    const newRefreshToken = "new-refresh-tok";

    const mockFetch = vi
      .fn()
      // First call: token refresh endpoint
      .mockResolvedValueOnce(
        makeResponse({ access_token: newAccessToken, refresh_token: newRefreshToken })
      )
      // Second call: the actual request
      .mockResolvedValueOnce(makeResponse({ data: "ok" }));

    const { apiFetch } = await setupApiFetch({
      accessToken: expiredToken,
      refreshToken: "old-refresh-tok",
      fetchImpl: mockFetch,
    });

    const response = await apiFetch("http://localhost:8181/chat");
    expect(response.status).toBe(200);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("redirects to /login when refresh fails", async () => {
    const expiredToken = makeJwt({ sub: "u1", exp: nowSec() - 60 });

    const mockFetch = vi
      .fn()
      // Refresh attempt returns 401
      .mockResolvedValueOnce(makeResponse({ detail: "Unauthorized" }, 401));

    const locationObj = { href: "" };
    vi.stubGlobal("window", { location: locationObj });

    const { apiFetch } = await setupApiFetch({
      accessToken: expiredToken,
      refreshToken: "bad-refresh-tok",
      fetchImpl: mockFetch,
    });

    const response = await apiFetch("http://localhost:8181/chat");
    // Should return synthetic 401
    expect(response.status).toBe(401);
  });

  it("clears tokens and redirects on 401 response from server", async () => {
    const token = makeJwt({ sub: "u1", exp: nowSec() + 3600 });
    const store: Record<string, string> = { auth_access_token: token };

    vi.resetModules();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v; },
      removeItem: (k: string) => { delete store[k]; },
    });

    const mockFetch = vi.fn().mockResolvedValue(makeResponse({ detail: "Unauthorized" }, 401));
    vi.stubGlobal("fetch", mockFetch);

    const locationObj = { href: "" };
    vi.stubGlobal("window", { location: locationObj });

    const { apiFetch } = await import("../lib/apiFetch");
    const response = await apiFetch("http://localhost:8181/protected");

    expect(response.status).toBe(401);
    expect(store["auth_access_token"]).toBeUndefined();
  });

  it("passes through non-401 error responses unchanged", async () => {
    const token = makeJwt({ sub: "u1", exp: nowSec() + 3600 });
    const mockFetch = vi
      .fn()
      .mockResolvedValue(makeResponse({ detail: "Not found" }, 404));

    const { apiFetch } = await setupApiFetch({
      accessToken: token,
      fetchImpl: mockFetch,
    });

    const response = await apiFetch("http://localhost:8181/missing");
    expect(response.status).toBe(404);
  });
});
