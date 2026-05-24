"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { SweepableField } from "@/lib/types/algoSweep";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useSweepableFields() {
  const { data, error, isLoading } = useSWR<
    { fields: SweepableField[] }
  >(
    `${API_URL}/algo/sweep/fields`,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );
  return {
    fields: data?.fields ?? [],
    isLoading,
    error,
  };
}
