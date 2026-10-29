/**
 * Regression test for the 2026-10-29 infinite-reload-loop bug.
 *
 * proxy.ts (the Next edge middleware) already bounces an
 * authenticated visitor away from /login server-side, checking
 * only the access_token/refresh_token HttpOnly cookie — this
 * page component never mounts in that case. The ONLY way it
 * mounts while localStorage still holds a non-expired access
 * token is when that token is ORPHANED (desynced from the
 * cookie). The page used to trust that stale token anyway and
 * navigate to /dashboard, which the proxy's cookie gate then
 * bounced straight back to /login — a real, observed infinite
 * reload loop (hundreds of requests/minute in one open tab).
 *
 * The fix: on mount, an orphaned token is cleared, never acted
 * on — the login form renders normally, breaking the loop.
 *
 * `@/lib/auth` is mocked rather than exercised against a real
 * localStorage — this project's current vitest+jsdom setup
 * doesn't provide `window.localStorage` (a Node experimental
 * `localStorage` global shadows jsdom's own, pre-existing
 * environment gap, unrelated to this fix), and mocking the
 * module boundary tests the same contract (does the component
 * call `clearTokens()`, does it ever try to navigate) without
 * depending on that polyfill.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("next/image", () => ({
  default: (props: Record<string, unknown>) => {
    // eslint-disable-next-line @next/next/no-img-element
    return <img alt={props.alt as string} />;
  },
}));

const getAccessTokenMock = vi.fn<() => string | null>();
const isTokenExpiredMock = vi.fn<(token: string | null) => boolean>();
const clearTokensMock = vi.fn();
const setTokensMock = vi.fn();

vi.mock("@/lib/auth", () => ({
  getAccessToken: () => getAccessTokenMock(),
  isTokenExpired: (token: string | null) => isTokenExpiredMock(token),
  clearTokens: () => clearTokensMock(),
  setTokens: (access: string) => setTokensMock(access),
}));

import LoginPage from "./page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LoginPage — orphaned-token loop regression", () => {
  it("clears the token and still fetches oauth providers when it is non-expired but orphaned", async () => {
    // The old, buggy branch called window.location.href and
    // `return`ed before reaching the oauth-providers fetch below
    // — so "fetch was called" is itself proof execution fell
    // through past the orphaned-token branch instead of
    // navigating away.
    getAccessTokenMock.mockReturnValue("orphaned.jwt.token");
    isTokenExpiredMock.mockReturnValue(false);
    vi.spyOn(global, "fetch").mockResolvedValue({
      json: async () => ({ providers: [] }),
    } as Response);

    render(<LoginPage />);

    await waitFor(() => {
      expect(clearTokensMock).toHaveBeenCalledTimes(1);
      expect(global.fetch).toHaveBeenCalled();
    });
  });

  it("renders the login form instead of a blank/redirecting page", async () => {
    getAccessTokenMock.mockReturnValue("orphaned.jwt.token");
    isTokenExpiredMock.mockReturnValue(false);
    vi.spyOn(global, "fetch").mockResolvedValue({
      json: async () => ({ providers: [] }),
    } as Response);

    render(<LoginPage />);

    expect(screen.getByTestId("login-submit-button")).toBeDefined();
  });

  it("behaves unchanged for an ordinary logged-out visit (no token)", async () => {
    getAccessTokenMock.mockReturnValue(null);
    isTokenExpiredMock.mockReturnValue(true);
    vi.spyOn(global, "fetch").mockResolvedValue({
      json: async () => ({ providers: [] }),
    } as Response);

    render(<LoginPage />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    expect(clearTokensMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("login-submit-button")).toBeDefined();
  });

  it("does not clear a token that is genuinely expired (no-op path)", async () => {
    getAccessTokenMock.mockReturnValue("expired.jwt.token");
    isTokenExpiredMock.mockReturnValue(true);
    vi.spyOn(global, "fetch").mockResolvedValue({
      json: async () => ({ providers: [] }),
    } as Response);

    render(<LoginPage />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    expect(clearTokensMock).not.toHaveBeenCalled();
  });
});
