"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { BrokerStatusResponse } from "@/lib/types/algoBroker";

const KEY = `${API_URL}/algo/broker/status`;

async function fetcher(url: string): Promise<BrokerStatusResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useBrokerStatus() {
  const { data, error, isLoading } = useSWR<BrokerStatusResponse>(
    KEY,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: 60_000,  // poll every minute
    },
  );
  return {
    value: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load broker status"
      : null,
  };
}

export async function saveApiKey(apiKey: string): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/broker/api-key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  await mutate(KEY);
}

export async function getLoginUrl(): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/broker/login`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { url: string };
  return body.url;
}

export async function disconnectBroker(): Promise<void> {
  const r = await apiFetch(`${API_URL}/algo/broker`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  await mutate(KEY);
}
