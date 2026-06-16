// Types + defaults extracted from StockChart.tsx so consumers
// (e.g. analysis/page.tsx) can reference them without pulling
// `lightweight-charts` (~150 KB) into the initial bundle.

export type ChartInterval = "D" | "W" | "M";

export interface IndicatorVisibility {
  sma5: boolean;
  sma10: boolean;
  sma20: boolean;
  sma50: boolean;
  sma200: boolean;
  bollinger: boolean;
  volume: boolean;
  rsi: boolean;
  rsi2: boolean;
  macd: boolean;
  supportResistance: boolean;
}

export const DEFAULT_INDICATORS: IndicatorVisibility = {
  // Off by default — opt-in via the Indicators toggle so the
  // price pane isn't cluttered with 5 overlapping MA lines.
  sma5: false,
  sma10: false,
  sma20: false,
  sma50: true,
  sma200: true,
  bollinger: false,
  volume: false,
  rsi: true,
  rsi2: false,
  macd: true,
  supportResistance: false,
};
