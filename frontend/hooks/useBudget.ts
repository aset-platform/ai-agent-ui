"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  BudgetReservationView, UserBudgetView,
} from "@/lib/types/algoBudget";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useUserBudget() {
  const { data, error, isLoading, mutate } = useSWR<
    UserBudgetView
  >(
    `${API_URL}/algo/budget`,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 5_000 },
  );
  return { budget: data, error, isLoading, mutate };
}

export function useActiveReservations() {
  const { data, error, isLoading, mutate } = useSWR<
    { reservations: BudgetReservationView[] }
  >(
    `${API_URL}/algo/budget/reservations`,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        !latest || latest.reservations.length === 0
          ? 0
          : 3_000,
    },
  );
  return {
    reservations: data?.reservations ?? [],
    error,
    isLoading,
    mutate,
  };
}

export async function setAllocation(
  newAllocation: string,
): Promise<UserBudgetView> {
  const r = await apiFetch(
    `${API_URL}/algo/budget/allocation`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        allocated_inr: newAllocation,
      }),
    },
  );
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Allocation failed: ${body}`);
  }
  return r.json();
}

export async function forceReleaseReservation(
  reservationId: string,
): Promise<void> {
  const r = await apiFetch(
    `${API_URL}/algo/budget/reservations/`
    + `${reservationId}/force-release`,
    { method: "POST" },
  );
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Release failed: ${body}`);
  }
}
