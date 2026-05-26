// Mirrors backend/algo/routes/portfolio.py.

export interface AlgoPositionView {
  tradingsymbol: string;
  internal_ticker: string;
  product: "MIS" | "CNC";
  quantity: number;
  avg_price: string;        // Decimal as string
  last_price: string;
  pnl_inr: string;
  pnl_pct: string;
  strategy_id: string;
  strategy_name: string;
  entry_ts: string | null;
  days_held: number;
  t1_pending: boolean;
  // "live" = Kite-held; "paper" = synthesized from
  // mode='paper' algo.events fills. Defaults to "live"
  // when older backends omit the field.
  source?: "live" | "paper";
}

export interface AlgoPositionsResponse {
  positions: AlgoPositionView[];
  as_of: string;
  market_open: boolean;
}
