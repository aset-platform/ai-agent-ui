"use client";
/**
 * LiveLandedOrdersList — V2-5.
 *
 * Shows in-flight Kite orders for the selected strategy.
 * Polling every 10s (handled in useLiveOrders).
 */

import { formatIstTime } from "@/lib/datetime";
import type { InFlightOrder } from "@/hooks/useLiveOrders";
import { useLiveOrders } from "@/hooks/useLiveOrders";

interface Props {
  strategyId: string;
}

export function LiveLandedOrdersList({ strategyId }: Props) {
  const { orders, loading, error } = useLiveOrders(strategyId);

  if (loading && orders.length === 0) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="live-orders-loading"
      >
        Loading in-flight orders…
      </p>
    );
  }

  if (error) {
    return (
      <p
        className="text-xs text-rose-600"
        data-testid="live-orders-error"
      >
        {error}
      </p>
    );
  }

  const submitted = orders.filter((o) => o.status === "submitted");

  if (submitted.length === 0) {
    return (
      <p
        className="text-xs text-slate-500"
        data-testid="live-orders-empty"
      >
        No in-flight orders.
      </p>
    );
  }

  return (
    <ul
      className="space-y-1"
      data-testid="live-orders-list"
    >
      {submitted.map((order: InFlightOrder) => (
        <li
          key={order.kite_order_id}
          className="flex items-center justify-between rounded border
            border-slate-200 px-3 py-1.5 text-sm
            dark:border-slate-700"
          data-testid={`live-order-${order.kite_order_id}`}
        >
          <div className="flex items-center gap-2">
            <span
              className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium
                ${order.side === "BUY"
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300"
                  : "bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-300"
                }`}
            >
              {order.side}
            </span>
            <span className="font-medium text-slate-900 dark:text-slate-100">
              {order.symbol}
            </span>
            <span className="text-slate-500">×{order.qty}</span>
          </div>
          <div className="text-[11px] text-slate-400">
            {formatIstTime(order.submitted_at)}
            {" · "}
            <span className="text-slate-500">{order.kite_order_id}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
