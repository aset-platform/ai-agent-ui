// frontend/lib/types/algoTrading.ts
/**
 * Type literals for the Algo Trading module.
 *
 * Three-page split (Slice 2 of the algo-trading-three-page-split
 * epic):
 *   - StrategiesTabId — `/algo-trading/strategies` tab strip
 *   - LiveTabId       — `/algo-trading/live` tab strip
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
export type LiveTabId =
  | "live"
  | "positions"
  | "holdings"
  | "postbacks"
  | "settings";

export const LIVE_TAB_LABELS: Record<LiveTabId, string> = {
  live: "Live",
  positions: "Positions",
  holdings: "Holdings",
  postbacks: "Postbacks",
  settings: "Settings",
};

export const LIVE_TAB_ORDER: LiveTabId[] = [
  "live",
  "positions",
  "holdings",
  "postbacks",
  "settings",
];
