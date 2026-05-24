// Mirrors auth/endpoints/ticker_routes.py.

export interface BulkTickerErrorRow {
  row: number;
  ticker: string;
  reason: string;
}

export interface BulkTickerResponse {
  added: string[];
  skipped_already_linked: string[];
  errors: BulkTickerErrorRow[];
  total_rows: number;
}

export interface UnlinkAllResponse {
  removed: number;
}
