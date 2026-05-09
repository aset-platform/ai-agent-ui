"use client";
/**
 * Kite OAuth callback bounce page.
 *
 * Configure your Kite Connect app's redirect URL to:
 *   http://localhost:3000/algo-trading/kite-callback
 *
 * Kite redirects the browser here with ?request_token=...&action=login&status=success
 * after successful auth. We forward the request_token to the
 * backend via apiFetch (which carries the JWT) and then bounce
 * back to the Connect Broker tab.
 */

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

type State =
  | { kind: "exchanging" }
  | { kind: "success"; kiteUserId: string | null }
  | { kind: "error"; detail: string };

export default function KiteCallbackPage() {
  const [state, setState] = useState<State>({ kind: "exchanging" });

  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const requestToken = sp.get("request_token");
    const status = sp.get("status");

    if (status && status !== "success") {
      void Promise.resolve().then(() => {
        setState({
          kind: "error",
          detail: `Kite reported status="${status}".`,
        });
      });
      return;
    }
    if (!requestToken) {
      void Promise.resolve().then(() => {
        setState({
          kind: "error",
          detail: "Missing request_token in callback URL.",
        });
      });
      return;
    }

    const url = `${API_URL}/algo/broker/callback?request_token=${encodeURIComponent(
      requestToken,
    )}`;
    apiFetch(url)
      .then(async (r) => {
        if (!r.ok) {
          let detail = `HTTP ${r.status}`;
          try {
            const body = await r.json();
            if (body?.detail) detail = body.detail;
          } catch {
            // ignore
          }
          setState({ kind: "error", detail });
          return;
        }
        const body = (await r.json()) as {
          status: string;
          kite_user_id: string | null;
        };
        setState({
          kind: "success",
          kiteUserId: body.kite_user_id,
        });
        // Auto-redirect after a brief pause so the user sees
        // the success state.
        setTimeout(() => {
          window.location.href = "/algo-trading?tab=connect";
        }, 1500);
      })
      .catch((exc) => {
        setState({
          kind: "error",
          detail:
            exc instanceof Error
              ? exc.message
              : "Failed to exchange request_token",
        });
      });
  }, []);

  return (
    <div className="space-y-3 p-6" data-testid="kite-callback-page">
      <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">
        Connecting Zerodha
      </h1>

      {state.kind === "exchanging" && (
        <p
          className="text-sm text-slate-600 dark:text-slate-400"
          data-testid="kite-callback-exchanging"
        >
          Exchanging request_token with Kite…
        </p>
      )}

      {state.kind === "success" && (
        <div
          className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300"
          data-testid="kite-callback-success"
        >
          Connected
          {state.kiteUserId ? ` as ${state.kiteUserId}` : ""}.
          Redirecting to the Connect Broker tab…
        </div>
      )}

      {state.kind === "error" && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300"
          data-testid="kite-callback-error"
        >
          <p className="font-medium">Connection failed</p>
          <p className="mt-1">{state.detail}</p>
          <a
            href="/algo-trading?tab=connect"
            className="mt-2 inline-block text-indigo-600 hover:underline"
          >
            Back to Connect Broker
          </a>
        </div>
      )}
    </div>
  );
}
