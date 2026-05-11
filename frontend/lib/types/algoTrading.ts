// frontend/lib/types/algoTrading.ts
/**
 * Type literals for the Algo Trading module.
 *
 * Three-page split (Slice 2 of the algo-trading-three-page-split
 * epic):
 *   - StrategiesTabId — `/algo-trading/strategies` tab strip
 *   - LiveTabId       — `/algo-trading/live` tab strip
 *
 * `AlgoTabId` (the legacy single-page union) is kept as a deprecated
 * alias purely so the orphaned `AlgoTradingClient.tsx` continues to
 * type-check until Slice 5 removes it.
 */

/** Strategies-page tab IDs. URL-synced via ?tab=. */
export type StrategiesTabId =
  | "instruments"
  | "strategies"
  | "backtest"
  | "paper"
  | "dryrun"
  | "performance"
  | "replay"
  | "settings";

export const STRATEGIES_TAB_LABELS: Record<StrategiesTabId, string> = {
  instruments: "Instruments",
  strategies: "Strategies",
  backtest: "Backtest",
  paper: "Paper",
  dryrun: "Dry run",
  performance: "Performance",
  replay: "Replay",
  settings: "Settings",
};

export const STRATEGIES_TAB_ORDER: StrategiesTabId[] = [
  "instruments",
  "strategies",
  "backtest",
  "paper",
  "dryrun",
  "performance",
  "replay",
  "settings",
];

/** Live-page tab IDs. URL-synced via ?tab=. */
export type LiveTabId = "live" | "positions" | "holdings" | "settings";

export const LIVE_TAB_LABELS: Record<LiveTabId, string> = {
  live: "Live",
  positions: "Positions",
  holdings: "Holdings",
  settings: "Settings",
};

export const LIVE_TAB_ORDER: LiveTabId[] = [
  "live",
  "positions",
  "holdings",
  "settings",
];

/**
 * @deprecated Legacy single-page tab union. The orphaned
 * `AlgoTradingClient.tsx` still references these; both the file and
 * these exports will be removed in Slice 5.
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

/** @deprecated See `AlgoTabId`. */
export const ALGO_TAB_LABELS: Record<AlgoTabId, string> = {
  connect: "Connect Broker",
  instruments: "Instruments",
  strategies: "Strategies",
  backtest: "Backtest",
  paper: "Trading",
  performance: "Performance",
  replay: "Replay",
  settings: "Settings",
};

/** @deprecated See `AlgoTabId`. */
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
