"use client";

import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

interface Props {
  onClose: () => void;
}

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function BudgetReservationHistoryModal(
  { onClose }: Props,
) {
  const { data, isLoading } = useSWR<{
    reservations: Record<string, string>[];
  }>(
    `${API_URL}/algo/budget/reservations`
    + `?include_history=true`,
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 overflow-y-auto"
      data-testid="budget-reservation-history-modal"
    >
      <div className="bg-white dark:bg-slate-900 rounded-md p-4 w-[800px] max-w-[95vw] my-8 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">
            Reservation history
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-xs underline"
          >
            Close
          </button>
        </div>
        {isLoading && (
          <p className="text-xs text-slate-500">
            Loading…
          </p>
        )}
        {!isLoading && data && (
          <div className="max-h-[60vh] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800 sticky top-0">
                <tr>
                  <th className="px-2 py-1 text-left">Time</th>
                  <th className="px-2 py-1 text-left">Ticker</th>
                  <th className="px-2 py-1 text-left">Side</th>
                  <th className="px-2 py-1 text-right">Qty</th>
                  <th className="px-2 py-1 text-left">State</th>
                  <th className="px-2 py-1 text-right">Reserved</th>
                  <th className="px-2 py-1 text-right">Filled</th>
                </tr>
              </thead>
              <tbody>
                {data.reservations.map(
                  (r: Record<string, string>) => (
                    <tr
                      key={`${r.reservation_id}-${r.transitioned_at}`}
                      className="border-t"
                    >
                      <td className="px-2 py-1">
                        {r.transitioned_at}
                      </td>
                      <td className="px-2 py-1">{r.ticker}</td>
                      <td className="px-2 py-1">{r.side}</td>
                      <td className="px-2 py-1 text-right">
                        {r.qty}
                      </td>
                      <td className="px-2 py-1">{r.state}</td>
                      <td className="px-2 py-1 text-right">
                        ₹{r.reserved_inr}
                      </td>
                      <td className="px-2 py-1 text-right">
                        ₹{r.filled_inr}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
