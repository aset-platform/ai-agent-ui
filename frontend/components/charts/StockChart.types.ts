// Types + defaults extracted from StockChart.tsx so consumers
// (e.g. analysis/page.tsx) can reference them without pulling
// `lightweight-charts` (~150 KB) into the initial bundle.

export type ChartInterval = "D" | "W" | "M";

export interface IndicatorVisibility {
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
  sma50: true,
  sma200: true,
  bollinger: false,
  volume: false,
  rsi: true,
  rsi2: false,
  macd: true,
  supportResistance: false,
};
