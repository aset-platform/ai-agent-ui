"use client";

import { useState, useEffect, useRef, FormEvent } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { getAccessToken, isTokenExpired, setTokens } from "@/lib/auth";
import {
  generateCodeChallenge,
  generateCodeVerifier,
  storeOAuthSession,
} from "@/lib/oauth";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8181";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [oauthProviders, setOauthProviders] = useState<string[]>([]);
  const [oauthLoading, setOauthLoading] = useState<string | null>(null);
  // Fix #16: abort ref to cancel login request if component unmounts mid-flight
  const loginAbortRef = useRef<AbortController | null>(null);

  // If already authenticated, skip straight to the app.
  useEffect(() => {
    const token = getAccessToken();
    if (token && !isTokenExpired(token)) {
      router.replace("/");
      return;
    }
    // Fix #15: AbortController so the fetch is cancelled if the component unmounts
    const controller = new AbortController();
    fetch(`${BACKEND_URL}/auth/oauth/providers`, { signal: controller.signal })
      .then((r) => r.json())
      .then((data: { providers?: string[] }) => {
        setOauthProviders(
          (data.providers ?? []).filter((p) => p !== "facebook")
        );
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        // Backend unreachable — hide SSO buttons gracefully.
        setOauthProviders([]);
      });
    return () => controller.abort();
  }, [router]);

  const handleOAuthLogin = async (provider: string) => {
    if (oauthLoading) return;
    setError("");
    setOauthLoading(provider);

    try {
      // 1. Generate PKCE verifier + challenge client-side.
      const verifier = generateCodeVerifier();
      const challenge = await generateCodeChallenge(verifier);

      // 2. Fetch the provider's authorize URL from the backend.
      const res = await fetch(
        `${BACKEND_URL}/auth/oauth/${provider}/authorize?code_challenge=${encodeURIComponent(challenge)}`
      );

      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        setError(body.detail ?? `Could not start ${provider} sign-in.`);
        return;
      }

      const data = (await res.json()) as {
        state: string;
        authorize_url: string;
      };

      // 3. Persist verifier + provider for the callback page.
      storeOAuthSession(provider, verifier);

      // 4. Redirect the browser to the provider's consent page.
      window.location.href = data.authorize_url;
    } catch {
      setError("Could not reach the server. Is the backend running?");
    } finally {
      setOauthLoading(null);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setError("");
    setLoading(true);

    // Fix #16: cancel any previous in-flight login request
    loginAbortRef.current?.abort();
    const controller = new AbortController();
    loginAbortRef.current = controller;

    try {
      const res = await fetch(`${BACKEND_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        signal: controller.signal,
      });

      if (!res.ok) {
        // Never reveal whether the email or password was wrong.
        setError("Invalid email or password.");
        return;
      }

      const data = (await res.json()) as {
        access_token: string;
        refresh_token: string;
      };
      setTokens(data.access_token, data.refresh_token);
      router.replace("/");
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError("Could not reach the server. Is the backend running?");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 font-sans">
      <div className="w-full max-w-sm">
        {/* Logo / brand */}
        <div className="flex flex-col items-center mb-8 gap-4">
          <Image
            src="/images/aset-logo-final.svg"
            alt="ASET"
            width={64}
            height={64}
            className="h-16 w-auto drop-shadow-sm"
            priority
          />
          <p className="text-sm text-gray-500">Sign in to continue</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-200 px-6 py-8">
          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email */}
            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-gray-700 mb-1.5"
              >
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition"
              />
            </div>

            {/* Password */}
            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-gray-700 mb-1.5"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition"
              />
            </div>

            {/* Error message */}
            {error && (
              <div className="flex items-start gap-2 rounded-xl bg-red-50 border border-red-100 px-4 py-3">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="w-4 h-4 text-red-500 shrink-0 mt-0.5"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                <p className="text-sm text-red-600">{error}</p>
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !email || !password}
              className="w-full rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-200 disabled:cursor-not-allowed text-white font-medium py-2.5 text-sm transition-colors shadow-sm flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Signing in…
                </>
              ) : (
                "Sign in"
              )}
            </button>
          </form>

          {/* OAuth SSO buttons — only shown when providers are configured */}
          {oauthProviders.length > 0 && (
            <>
              {/* Divider */}
              <div className="flex items-center gap-3 my-5">
                <hr className="flex-1 border-gray-200" />
                <span className="text-xs text-gray-400">or continue with</span>
                <hr className="flex-1 border-gray-200" />
              </div>

              <div className="flex flex-col gap-2.5">
                {oauthProviders.includes("google") && (
                  <button
                    onClick={() => handleOAuthLogin("google")}
                    disabled={!!oauthLoading}
                    className="w-full flex items-center justify-center gap-3 rounded-xl border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors shadow-sm"
                  >
                    {oauthLoading === "google" ? (
                      <span className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                    ) : (
                      /* Google 'G' logo */
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 48 48"
                        className="w-4 h-4 shrink-0"
                      >
                        <path
                          fill="#EA4335"
                          d="M24 9.5c3.5 0 6.6 1.2 9.1 3.2l6.8-6.8C35.8 2.3 30.3 0 24 0 14.6 0 6.6 5.5 2.6 13.5l7.9 6.1C12.4 13.3 17.8 9.5 24 9.5z"
                        />
                        <path
                          fill="#4285F4"
                          d="M46.5 24.5c0-1.6-.1-3.2-.4-4.7H24v8.9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 6.9-10.1 6.9-17.2z"
                        />
                        <path
                          fill="#FBBC05"
                          d="M10.5 28.4a14.6 14.6 0 0 1 0-8.8l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l7.9-6.3z"
                        />
                        <path
                          fill="#34A853"
                          d="M24 48c6.3 0 11.6-2.1 15.5-5.6l-7.5-5.8c-2.1 1.4-4.8 2.2-7.9 2.2-6.2 0-11.5-3.8-13.4-9.2l-7.9 6.3C6.6 42.5 14.6 48 24 48z"
                        />
                        <path fill="none" d="M0 0h48v48H0z" />
                      </svg>
                    )}
                    Sign in with Google
                  </button>
                )}

                {oauthProviders.includes("facebook") && (
                  <button
                    onClick={() => handleOAuthLogin("facebook")}
                    disabled={!!oauthLoading}
                    className="w-full flex items-center justify-center gap-3 rounded-xl border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors shadow-sm"
                  >
                    {oauthLoading === "facebook" ? (
                      <span className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                    ) : (
                      /* Facebook 'f' logo */
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 24 24"
                        className="w-4 h-4 shrink-0"
                        fill="#1877F2"
                      >
                        <path d="M24 12.073C24 5.405 18.627 0 12 0S0 5.405 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.413c0-3.025 1.792-4.697 4.533-4.697 1.312 0 2.686.235 2.686.235v2.97h-1.513c-1.491 0-1.956.93-1.956 1.883v2.268h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073z" />
                      </svg>
                    )}
                    Sign in with Facebook
                  </button>
                )}
              </div>
            </>
          )}
        </div>

        <p className="text-center text-xs text-gray-400 mt-6">
          AI Agent UI · Powered by Claude
        </p>
      </div>
    </div>
  );
}
