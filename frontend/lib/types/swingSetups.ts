/**
 * Swing Setups types — mirrors
 * `backend/advanced_analytics_models.py` SwingSetupsResponse
 * and `backend/advanced_analytics_swing.build_methodology()`.
 */

import type { AdvancedRow } from "./advancedAnalytics";

export type SwingRegime = "bull" | "sideways" | "bearish";

export interface SwingMethodologyGate {
  label: string;
  rule: string;
  why: string;
}

export interface SwingMethodologyRank {
  formula: string;
  direction: "ASC" | "DESC";
  cap: number;
  degraded: string | null;
}

export interface SwingMethodology {
  regime: SwingRegime;
  summary: string;
  gates: SwingMethodologyGate[];
  rank: SwingMethodologyRank;
}

export interface SwingSetupsResponse {
  rows: AdvancedRow[];
  total: number;
  regime: SwingRegime;
  as_of: string;
  rec_gate_applied: boolean;
  rec_run_id: string | null;
  rec_run_date: string | null;
  notes: string[];
  methodology: SwingMethodology;
}
