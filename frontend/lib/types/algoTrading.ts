// frontend/lib/types/algoTrading.ts
/**
 * Type literals for the Algo Trading module.
 * Tab IDs map 1-to-1 with the spec § 2.2 tab strip.
 */

export type AlgoTabId =
  | "connect"
  | "instruments"
  | "strategies"
  | "backtest"
  | "paper"
  | "performance"
  | "replay"
  | "settings";

export const ALGO_TAB_LABELS: Record<AlgoTabId, string> = {
  connect: "Connect Broker",
  instruments: "Instruments",
  strategies: "Strategies",
  backtest: "Backtest",
  paper: "Paper Trading",
  performance: "Performance",
  replay: "Replay",
  settings: "Settings",
};

export const ALGO_TAB_ORDER: AlgoTabId[] = [
  "connect",
  "instruments",
  "strategies",
  "backtest",
  "paper",
  "performance",
  "replay",
  "settings",
];
