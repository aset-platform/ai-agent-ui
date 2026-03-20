"use client";

/**
 * OAuth2 callback page.
 *
 * The OAuth provider redirects here after the user consents:
 *   http://localhost:3000/auth/oauth/callback?code=...&state=...
 *
 * On mount this page:
 * 1. Reads `code` and `state` from the URL search params.
 * 2. Reads `code_verifier` and `provider` from `sessionStorage`.
 * 3. POSTs to `POST /auth/oauth/callback` on the backend.
 * 4. Stores the returned JWT pair via `setTokens()`.
 * 5. Redirects to `/` (the main app).
 *
 * An error banner is shown if any step fails (invalid state, rejected code,
 * network error, etc.).
 */

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { setTokens } from "@/lib/auth";
import {
  clearOAuthSession,
  getStoredProvider,
  getStoredVerifier,
} from "@/lib/oauth";
import { API_URL } from "@/lib/config";

function OAuthCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<"loading" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- OAuth callback must set error/status state from validation + async fetch */
    // Fix #5: cancelled flag prevents state updates after unmount (avoids
    // React "can't perform state update on unmounted component" warnings and
    // duplicate state mutations on fast remount during strict-mode double-invoke).
    let cancelled = false;

    const code = searchParams.get("code");
    const state = searchParams.get("state");

    if (!code || !state) {
      setErrorMsg(
        "Missing authorization code or state parameter. Please try signing in again."
      );
      setStatus("error");
      return () => { cancelled = true; };
    }

    const provider = getStoredProvider();
    const codeVerifier = getStoredVerifier();

    if (!provider) {
      setErrorMsg(
        "OAuth session data not found. Please try signing in again."
      );
      setStatus("error");
      return () => { cancelled = true; };
    }

    // Exchange the code for our own JWT pair.
    (async () => {
      try {
        const res = await fetch(`${API_URL}/auth/oauth/callback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            provider,
            code,
            state,
            code_verifier: codeVerifier ?? undefined,
          }),
        });

        if (cancelled) return;

        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as {
            detail?: string;
          };
          if (!cancelled) {
            setErrorMsg(body.detail ?? "Sign-in failed. Please try again.");
            setStatus("error");
          }
          return;
        }

        const data = (await res.json()) as {
          access_token: string;
          refresh_token: string;
        };

        if (cancelled) return;

        // Refresh token is now in HttpOnly cookie.
        setTokens(data.access_token);
        clearOAuthSession();

        // Redirect to the main app.
        router.replace("/dashboard");
      } catch {
        if (!cancelled) {
          setErrorMsg("Could not reach the server. Is the backend running?");
          setStatus("error");
        }
      }
    })();

    return () => { cancelled = true; };
  }, [searchParams, router]);

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 font-sans">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-gray-500">Completing sign-in…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 font-sans">
      <div className="w-full max-w-sm">
        <div className="bg-white rounded-2xl shadow-sm border border-gray-200 px-6 py-8 text-center">
          <div className="flex flex-col items-center gap-4">
            {/* Error icon */}
            <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="w-6 h-6 text-red-500"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <div>
              <h2 className="text-base font-semibold text-gray-900">
                Sign-in failed
              </h2>
              <p className="text-sm text-gray-500 mt-1">{errorMsg}</p>
            </div>
            <button
              onClick={() => router.replace("/login")}
              className="mt-2 w-full rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2.5 text-sm transition-colors"
            >
              Back to login
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-gray-50 font-sans">
          <div className="flex flex-col items-center gap-4">
            <div className="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-gray-500">Completing sign-in…</p>
          </div>
        </div>
      }
    >
      <OAuthCallbackInner />
    </Suspense>
  );
}
