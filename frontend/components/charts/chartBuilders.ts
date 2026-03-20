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

/** Sentiment emoji based on forecast sentiment text. */
function sentimentEmoji(
  sentiment: string | null | undefined,
): string {
  if (!sentiment) return "";
  const s = sentiment.toLowerCase();
  if (s.includes("bull")) return " \u{1F7E2}";
  if (s.includes("bear")) return " \u{1F534}";
  return " \u{1F7E1}";
}

/** Build forecast chart shapes (today line, price line, targets). */
export function buildForecastShapes(
  currentPrice: number | null,
  targets: {
    horizon_months: number;
    target_date: string;
    target_price: number;
    pct_change: number;
  }[],
): { shapes: Partial<Plotly.Shape>[]; annotations: Partial<Plotly.Annotations>[] } {
  const today = new Date().toISOString().slice(0, 10);
  const shapes: Partial<Plotly.Shape>[] = [
    // "Today" vertical line
    {
      type: "line",
      x0: today,
      x1: today,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: {
        color: "rgba(107,114,128,0.5)",
        width: 1.5,
        dash: "dot",
      },
    },
  ];
  const annotations: Partial<Plotly.Annotations>[] = [
    // "Today" label
    {
      x: today,
      y: 1.02,
      yref: "paper",
      text: "Today",
      showarrow: false,
      font: { size: 10, color: "#9ca3af" },
    },
  ];

  // Current price horizontal line
  if (currentPrice != null) {
    shapes.push({
      type: "line",
      x0: 0,
      x1: 1,
      xref: "paper",
      y0: currentPrice,
      y1: currentPrice,
      line: {
        color: "rgba(107,114,128,0.4)",
        width: 1,
        dash: "dot",
      },
    });
    annotations.push({
      x: 1.0,
      xref: "paper",
      y: currentPrice,
      text: `Current: ${currentPrice.toFixed(2)}`,
      showarrow: false,
      xanchor: "left",
      font: { size: 9, color: "#9ca3af" },
    });
  }

  // Price target annotations on chart
  const targetColors = [
    "rgba(245,158,11,0.85)",
    "rgba(249,115,22,0.85)",
    "rgba(239,68,68,0.85)",
  ];
  targets.forEach((t, i) => {
    const sign = t.pct_change >= 0 ? "+" : "";
    annotations.push({
      x: t.target_date,
      y: t.target_price,
      text:
        `${t.horizon_months}M: ${t.target_price.toFixed(0)}` +
        ` (${sign}${t.pct_change.toFixed(1)}%)`,
      showarrow: true,
      arrowhead: 2,
      arrowsize: 0.8,
      arrowcolor:
        targetColors[i % targetColors.length],
      ax: 40 + i * 15,
      ay: -(30 + i * 15),
      bordercolor:
        targetColors[i % targetColors.length],
      borderwidth: 1,
      borderpad: 3,
      bgcolor: "rgba(255,255,255,0.9)",
      font: { size: 10 },
    });
  });

  return { shapes, annotations };
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
  sentiment?: string | null,
): Plotly.Data[] {
  const emoji = sentimentEmoji(sentiment);
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
      name: "80% Confidence",
      fill: "tonexty",
      fillcolor: "rgba(76,175,80,0.15)",
      line: { width: 0 },
    },
    // Forecast line
    {
      x: forecastDates,
      y: forecastPrices,
      type: "scatter",
      mode: "lines",
      name: `Forecast${emoji}`,
      line: {
        color: "#4caf50",
        width: 2,
        dash: "dash",
      },
    },
  ];
}
