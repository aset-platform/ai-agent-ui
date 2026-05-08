export type BrokerStatus =
  | "disconnected"
  | "key_set"
  | "connected"
  | "expired";

export interface BrokerStatusResponse {
  status: BrokerStatus;
  kite_user_id: string | null;
  last_login_at: string | null;
  access_token_expires_at: string | null;
}

export const BROKER_STATUS_LABEL: Record<BrokerStatus, string> = {
  disconnected: "Not connected",
  key_set: "API key saved — click Connect Zerodha",
  connected: "Connected",
  expired: "Re-auth required (Kite token expired)",
};
