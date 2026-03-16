/**
 * Chart builder utilities for common Plotly chart patterns.
 *
 * Mirrors patterns from ``dashboard/callbacks/chart_builders.py``
 * for the Dash-to-Next.js migration.
 */

import { CHART_COLORS } from "./PlotlyChart";

/** Build a price line chart from OHLCV data. */
export function buildPriceChart(
  dates: string[],
  closes: number[],
  ticker: string,
): Plotly.Data[] {
  return [
    {
      x: dates,
      y: closes,
      type: "scatter",
      mode: "lines",
      name: ticker,
      line: { color: CHART_COLORS[0], width: 2 },
      hovertemplate:
        "%{x}<br>%{y:.2f}<extra></extra>",
    },
  ];
}

/** Build RSI indicator chart. */
export function buildRSIChart(
  dates: string[],
  rsi: number[],
): Plotly.Data[] {
  return [
    {
      x: dates,
      y: rsi,
      type: "scatter",
      mode: "lines",
      name: "RSI 14",
      line: { color: CHART_COLORS[1], width: 1.5 },
      hovertemplate:
        "%{x}<br>RSI: %{y:.1f}<extra></extra>",
    },
  ];
}

/** Build MACD chart with signal line + histogram. */
export function buildMACDChart(
  dates: string[],
  macd: number[],
  signal: number[],
  hist: number[],
): Plotly.Data[] {
  return [
    {
      x: dates,
      y: macd,
      type: "scatter",
      mode: "lines",
      name: "MACD",
      line: { color: CHART_COLORS[0], width: 1.5 },
    },
    {
      x: dates,
      y: signal,
      type: "scatter",
      mode: "lines",
      name: "Signal",
      line: {
        color: CHART_COLORS[3],
        width: 1.5,
        dash: "dot",
      },
    },
    {
      x: dates,
      y: hist,
      type: "bar",
      name: "Histogram",
      marker: {
        color: hist.map((v) =>
          v >= 0 ? "#10b981" : "#ef4444",
        ),
      },
    },
  ];
}

/** Build a normalized price comparison chart. */
export function buildComparisonChart(
  series: {
    ticker: string;
    dates: string[];
    normalized: number[];
  }[],
): Plotly.Data[] {
  return series.map((s, i) => ({
    x: s.dates,
    y: s.normalized,
    type: "scatter" as const,
    mode: "lines" as const,
    name: s.ticker,
    line: {
      color: CHART_COLORS[i % CHART_COLORS.length],
      width: 2,
    },
    hovertemplate:
      `${s.ticker}<br>%{x}<br>%{y:.1f}%`
      + "<extra></extra>",
  }));
}

/** Build a correlation heatmap. */
export function buildCorrelationHeatmap(
  tickers: string[],
  matrix: number[][],
): Plotly.Data[] {
  return [
    {
      z: matrix,
      x: tickers,
      y: tickers,
      type: "heatmap",
      colorscale: [
        [0, "#ef4444"],
        [0.5, "#f8fafc"],
        [1, "#10b981"],
      ],
      zmin: -1,
      zmax: 1,
      hovertemplate:
        "%{x} vs %{y}<br>r = %{z:.2f}"
        + "<extra></extra>",
    },
  ];
}

/** Build a forecast chart with confidence band. */
export function buildForecastChart(
  historicalDates: string[],
  historicalPrices: number[],
  forecastDates: string[],
  forecastPrices: number[],
  upperBound: number[],
  lowerBound: number[],
  ticker: string,
): Plotly.Data[] {
  return [
    // Historical line
    {
      x: historicalDates,
      y: historicalPrices,
      type: "scatter",
      mode: "lines",
      name: `${ticker} Actual`,
      line: { color: CHART_COLORS[0], width: 2 },
    },
    // Upper bound (invisible — for band fill)
    {
      x: forecastDates,
      y: upperBound,
      type: "scatter",
      mode: "lines",
      name: "Upper",
      line: { width: 0 },
      showlegend: false,
    },
    // Lower bound with fill to upper
    {
      x: forecastDates,
      y: lowerBound,
      type: "scatter",
      mode: "lines",
      name: "Confidence",
      fill: "tonexty",
      fillcolor: "rgba(99,102,241,0.15)",
      line: { width: 0 },
    },
    // Forecast line
    {
      x: forecastDates,
      y: forecastPrices,
      type: "scatter",
      mode: "lines+markers",
      name: `${ticker} Forecast`,
      line: {
        color: CHART_COLORS[1],
        width: 2,
        dash: "dash",
      },
      marker: { size: 6 },
    },
  ];
}
