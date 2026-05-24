// Mirrors backend/algo/live/budget_types.py.

export type ReservationState =
  | "PENDING"
  | "SUBMITTED"
  | "FILLED"
  | "REJECTED"
  | "CANCELLED"
  | "PARTIAL"
  | "PARTIAL_CANCELLED"
  | "TIMEOUT";

export interface UserBudgetView {
  user_id: string;
  allocated_inr: string;
  enabled: boolean;
  open_pos_cost: string;
  active_reserved: string;
  internal_headroom: string;
  kite_available: string | null;
  available: string;
}

export interface BudgetReservationView {
  reservation_id: string;
  strategy_id: string;
  state: ReservationState;
  ticker: string;
  side: "BUY" | "SELL";
  qty: number;
  reserved_inr: string;
  filled_qty: number;
  filled_inr: string;
  kite_order_id: string | null;
  transitioned_at: string;
}
