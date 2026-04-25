/**
 * Next.js 16 edge proxy (renamed from middleware.ts).
 *
 * Two responsibilities:
 *
 * 1. Root redirect — `/` → `/dashboard` (preserved
 *    from the old middleware so deep-links and
 *    bookmarks keep working).
 * 2. Cookie-based auth gate — protected routes
 *    require the `access_token` HttpOnly cookie set
 *    by `/v1/auth/login` (ASETPLTFRM-334 phase A.1).
 *    Missing cookie → 302 to `/login` with a `next`
 *    param so the user lands back where they were
 *    after authenticating.
 *
 * The proxy only checks cookie *presence*, not JWT
 * signature. Reasons:
 *
 * - The backend re-verifies on every API call, so a
 *   stale/forged cookie can't actually access data.
 * - Avoids shipping a JWT lib (jose, ~28 KB) to the
 *   edge runtime + the cross-language secret-sharing
 *   problem (Python HMAC ↔ Node WebCrypto).
 * - Faster edge response — no async crypto.
 *
 * Authenticated route groups live under
 * `app/(authenticated)/*`. The matcher excludes
 * static assets, Next internals, and API rewrites
 * (which proxy to the backend on `/v1/*`).
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const ACCESS_COOKIE = "access_token";
// Sessions that pre-date phase A.1 (2026-04-25) only
// have the refresh_token cookie + a localStorage
// access token — no `access_token` cookie. Treat
// the refresh cookie as a sufficient signal that
// the user is authenticated; the client's next
// `apiFetch` call will trigger /v1/auth/refresh,
// which sets the new access_token cookie. Without
// this, those sessions infinite-loop between
// /login and /dashboard because the client thinks
// it's authenticated (localStorage) but the proxy
// thinks it isn't (no access_token cookie).
const REFRESH_COOKIE = "refresh_token";

// Routes that don't require auth. Anything else
// matched by `config.matcher` below is treated as
// protected.
const PUBLIC_PATHS = new Set([
  "/login",
  "/auth/forgot",
  "/auth/reset",
]);

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true;
  // Allow nested public auth routes
  // (e.g. /auth/oauth/...).
  return pathname.startsWith("/auth/");
}

function hasAuthCookie(request: NextRequest): boolean {
  return (
    request.cookies.has(ACCESS_COOKIE)
    || request.cookies.has(REFRESH_COOKIE)
  );
}

export default function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // Root → dashboard (preserved from old middleware).
  if (pathname === "/") {
    return NextResponse.redirect(
      new URL("/dashboard", request.url),
    );
  }

  if (isPublicPath(pathname)) {
    // Authenticated user landing on /login → bounce
    // them to /dashboard so they don't see the form
    // they don't need.
    if (pathname === "/login" && hasAuthCookie(request)) {
      return NextResponse.redirect(
        new URL("/dashboard", request.url),
      );
    }
    return NextResponse.next();
  }

  // Protected route — require either access or
  // refresh cookie. The refresh-only path is the
  // pre-phase-A.1 session shape; client refreshes
  // on first XHR and the access cookie lands.
  if (!hasAuthCookie(request)) {
    const loginUrl = new URL("/login", request.url);
    // Preserve where the user was trying to go so
    // they can be sent back after authentication.
    loginUrl.searchParams.set(
      "next",
      pathname + (search ?? ""),
    );
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

// Run on every navigation EXCEPT static assets,
// Next.js internals, and the `/v1/*` API rewrites
// (which already enforce auth backend-side).
export const config = {
  matcher: [
    "/((?!_next/static|_next/image|v1/|favicon\\.ico|.*\\.(?:png|jpg|jpeg|svg|webp|gif|woff2?|ico)$).*)",
  ],
};
