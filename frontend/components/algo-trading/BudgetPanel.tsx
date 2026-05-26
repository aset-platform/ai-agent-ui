"use client";

import { useState } from "react";
import {
  forceReleaseReservation,
  useActiveReservations,
  useUserBudget,
} from "@/hooks/useBudget";
import { BudgetAllocationModal }
  from "./BudgetAllocationModal";
import { BudgetReservationHistoryModal }
  from "./BudgetReservationHistoryModal";

export function BudgetPanel() {
  const {
    budget, isLoading, error, mutate: mutateBudget,
  } = useUserBudget();
  const {
    reservations, mutate: mutateReservations,
  } = useActiveReservations();
  const [editOpen, setEditOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmingId, setConfirmingId] = useState<
    string | null
  >(null);

  if (isLoading || !budget) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="budget-panel"
      >
        Loading budget…
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="rounded-md border border-rose-200 bg-rose-50 dark:bg-rose-950/30 p-4 text-sm text-rose-700"
        data-testid="budget-panel-error"
      >
        Budget unavailable
      </div>
    );
  }

  const allocated = Number(budget.allocated_inr);
  const open = Number(budget.open_pos_cost);
  const pending = Number(budget.active_reserved);
  const available = Number(budget.available);
  const kite = budget.kite_available
    ? Number(budget.kite_available)
    : null;

  async function handleForceRelease(id: string) {
    await forceReleaseReservation(id);
    setConfirmingId(null);
    await mutateReservations();
    await mutateBudget();
  }

  if (allocated === 0) {
    return (
      <>
        <div
          className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-2"
          data-testid="budget-panel"
        >
          <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
            ⚠ Algo trading is paused — no budget allocated.
          </p>
          <p className="text-xs text-amber-800 dark:text-amber-300">
            Set an algo allocation before enabling any
            strategy for live trading.
          </p>
          <button
            type="button"
            onClick={() => setEditOpen(true)}
            data-testid="budget-tile-edit-button"
            className="rounded bg-indigo-600 text-white px-3 py-1.5 text-sm"
          >
            Allocate budget
          </button>
        </div>
        {editOpen && (
          <BudgetAllocationModal
            current={budget}
            onClose={() => setEditOpen(false)}
            onSaved={() => mutateBudget()}
          />
        )}
      </>
    );
  }

  return (
    <>
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 space-y-3"
        data-testid="budget-panel"
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Budget</h3>
          <button
            type="button"
            onClick={() => setEditOpen(true)}
            data-testid="budget-tile-edit-button"
            className="text-xs underline"
          >
            Edit ✎
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Tile
            label="Allocated"
            value={`₹${allocated.toLocaleString("en-IN")}`}
            testid="budget-tile-allocated"
          />
          <Tile
            label="Open positions"
            value={`₹${open.toLocaleString("en-IN")}`}
            testid="budget-tile-open-positions"
          />
          <Tile
            label="Pending"
            value={`₹${pending.toLocaleString("en-IN")}`}
            testid="budget-tile-pending"
          />
          <Tile
            label="Available"
            value={`₹${available.toLocaleString("en-IN")}`}
            testid="budget-tile-available"
            accent="emerald"
          />
        </div>
        <div
          className="text-xs text-slate-500"
          data-testid="budget-kite-wallet-row"
        >
          Kite wallet:{" "}
          {kite != null
            ? `₹${kite.toLocaleString("en-IN")}`
            : (
              <span className="text-amber-700 dark:text-amber-300">
                Kite unreachable; using internal headroom only
              </span>
            )}{" "}
          {kite != null && (
            <span>
              ⓘ Live gate uses min(internal, Kite) = ₹
              {available.toLocaleString("en-IN")}
            </span>
          )}
        </div>

        {reservations.length > 0 && (
          <div
            className="rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden"
            data-testid="budget-active-reservations-table"
          >
            <table className="w-full text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700">
                <tr>
                  <th className="px-2 py-1 text-left">
                    Ticker
                  </th>
                  <th className="px-2 py-1 text-left">
                    Side
                  </th>
                  <th className="px-2 py-1 text-right">
                    Qty
                  </th>
                  <th className="px-2 py-1 text-right">
                    Reserved
                  </th>
                  <th className="px-2 py-1 text-left">
                    State
                  </th>
                  <th className="px-2 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {reservations.map((r) => (
                  <tr
                    key={r.reservation_id}
                    className="border-t border-slate-200 dark:border-slate-700"
                    data-testid={
                      `budget-reservation-row-${r.reservation_id}`
                    }
                  >
                    <td className="px-2 py-1">
                      <span>{r.ticker}</span>
                      {r.metadata?.mode === "paper" && (
                        <span
                          data-testid={
                            `budget-reservation-row-${r.reservation_id}-paper-badge`
                          }
                          className="ml-1 inline-block rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 text-[10px] font-semibold uppercase px-1 py-0.5 align-middle"
                          title="Paper-mode reservation (audit-only — does not deduct from real-money headroom)"
                        >
                          PAPER
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1">{r.side}</td>
                    <td className="px-2 py-1 text-right">
                      {r.qty}
                    </td>
                    <td className="px-2 py-1 text-right">
                      ₹{Number(
                        r.reserved_inr,
                      ).toLocaleString("en-IN")}
                    </td>
                    <td className="px-2 py-1">
                      {r.state}
                    </td>
                    <td className="px-2 py-1">
                      {confirmingId === r.reservation_id ? (
                        <span className="flex gap-1">
                          <button
                            type="button"
                            onClick={() =>
                              handleForceRelease(
                                r.reservation_id,
                              )
                            }
                            className="text-rose-600 underline"
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmingId(null)}
                            className="text-slate-400 underline"
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() =>
                            setConfirmingId(r.reservation_id)
                          }
                          data-testid={
                            `budget-force-release-button-${r.reservation_id}`
                          }
                          className="text-rose-500"
                        >
                          ✖
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <button
          type="button"
          onClick={() => setHistoryOpen(true)}
          data-testid="budget-reservation-history-link"
          className="text-xs underline text-slate-600 dark:text-slate-300"
        >
          View reservation history →
        </button>
      </div>

      {editOpen && (
        <BudgetAllocationModal
          current={budget}
          onClose={() => setEditOpen(false)}
          onSaved={() => mutateBudget()}
        />
      )}
      {historyOpen && (
        <BudgetReservationHistoryModal
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </>
  );
}

function Tile({
  label, value, testid, accent,
}: {
  label: string;
  value: string;
  testid: string;
  accent?: "emerald";
}) {
  const valCls =
    accent === "emerald"
      ? "text-lg font-bold text-emerald-600 dark:text-emerald-400"
      : "text-lg font-semibold";
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 px-3 py-2"
      data-testid={testid}
    >
      <p className="text-[11px] uppercase text-slate-400">
        {label}
      </p>
      <p className={valCls}>{value}</p>
    </div>
  );
}
