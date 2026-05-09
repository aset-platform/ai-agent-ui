"use client";
/**
 * Connect Broker tab — Slice 2 of the Algo Trading epic.
 *
 * Three-state UI driven by /v1/algo/broker/status:
 * - disconnected → API-key form
 * - key_set      → "Connect Zerodha" button (opens Kite login URL)
 * - connected    → success card with kite_user_id + Disconnect
 * - expired      → amber banner "Re-auth required" + same Connect button
 */

import { useCallback, useState } from "react";

import {
  disconnectBroker,
  getLoginUrl,
  saveApiKey,
  useBrokerStatus,
} from "@/hooks/useBrokerStatus";
import { BROKER_STATUS_LABEL } from "@/lib/types/algoBroker";

export function ConnectBrokerTab() {
  const { value, loading, error } = useBrokerStatus();
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleSaveKey = useCallback(async () => {
    if (!apiKey.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      await saveApiKey(apiKey.trim());
      setApiKey("");
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [apiKey]);

  const handleConnect = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      const url = await getLoginUrl();
      window.location.href = url;
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const handleDisconnect = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      await disconnectBroker();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  if (loading && !value) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }
  if (error) {
    return (
      <div role="alert" className="text-xs text-red-600 dark:text-red-400">
        {error}
      </div>
    );
  }

  const status = value?.status ?? "disconnected";

  return (
    <div className="space-y-4" data-testid="algo-connect-broker-tab">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Connect Broker
      </h2>
      <p className="text-sm text-gray-600 dark:text-gray-400">
        Connect your Zerodha account so paper-trading strategies
        can read live tick data. v1 is read-only — orders never
        leave the app, even with a valid token.
      </p>

      <div
        data-testid={`algo-broker-status-${status}`}
        className={`rounded-md border p-3 text-sm ${
          status === "connected"
            ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300"
            : status === "expired"
              ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300"
              : "border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-700 dark:bg-gray-800/40 dark:text-gray-300"
        }`}
      >
        {BROKER_STATUS_LABEL[status]}
        {value?.kite_user_id && (
          <span className="ml-2 text-xs text-gray-500">
            (Kite ID: {value.kite_user_id})
          </span>
        )}
      </div>

      {actionError && (
        <div role="alert" className="text-xs text-red-600 dark:text-red-400">
          {actionError}
        </div>
      )}

      {status === "disconnected" && (
        <div className="space-y-2 max-w-md">
          <label className="block text-xs font-semibold text-gray-700 dark:text-gray-200">
            Kite API key
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              data-testid="algo-broker-api-key-input"
              className="mt-1 w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5 text-sm font-mono"
              placeholder="api_key_xxx"
            />
          </label>
          <button
            type="button"
            onClick={handleSaveKey}
            disabled={busy || !apiKey.trim()}
            data-testid="algo-broker-save-key"
            className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 text-sm disabled:opacity-40"
          >
            {busy ? "Saving…" : "Save API key"}
          </button>
        </div>
      )}

      {(status === "key_set" || status === "expired") && (
        <button
          type="button"
          onClick={handleConnect}
          disabled={busy}
          data-testid="algo-broker-connect"
          className="rounded-md bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 text-sm disabled:opacity-40"
        >
          {busy ? "Opening Kite login…" : "Connect Zerodha"}
        </button>
      )}

      {(status === "connected" || status === "key_set" || status === "expired") && (
        <button
          type="button"
          onClick={handleDisconnect}
          disabled={busy}
          data-testid="algo-broker-disconnect"
          className="ml-2 rounded-md border border-gray-300 dark:border-gray-700 px-3 py-1.5 text-sm disabled:opacity-40"
        >
          Disconnect
        </button>
      )}
    </div>
  );
}
