# TradingView Lightweight Charts — All Chart Components

## Migration: Plotly → lightweight-charts v5
Replaced `plotly.js-basic-dist` (~3-8 MB) with TradingView's `lightweight-charts` (~45 KB).
Migration nearly complete — only Insights page still uses Plotly (sectors bar, correlation heatmap, quarterly).

## Chart Components

### `StockChart.tsx` — Multi-pane stock analysis
- Pane 1: Candlestick OHLC + SMA 50/200 + Bollinger Bands
- Pane 2: Volume histogram (green/red by close vs open)
- Pane 3: RSI (14) with 70/30 reference lines
- Pane 4: MACD line + Signal + Histogram

### `PortfolioChart.tsx` — Portfolio performance
- AreaSeries: market value (indigo gradient fill)
- LineSeries: invested value (amber dashed 2px)
- HistogramSeries: daily P&L (green/red, bottom 20%)
- Crosshair: date, value, invested, gain/loss %, daily P&L

### `PortfolioForecastChart.tsx` — Portfolio forecast
- Historical: LineSeries (indigo solid) + LineSeries (amber dashed, invested)
- Forecast: LineSeries (green dashed) + confidence band (two AreaSeries)
- Flat invested projection through forecast dates
- Crosshair: date, value, invested, gain/loss %, FORECAST tag

### `ForecastChart.tsx` — Per-ticker stock forecast
- LineSeries: historical price (indigo solid)
- LineSeries: forecast predicted (green dashed)
- Confidence band: AreaSeries upper (green fill) + AreaSeries lower (bg erase)
- Crosshair: date, price, confidence range on forecast dates

### `CompareChart.tsx` — Compare normalized prices
- One LineSeries per ticker with distinct colors from COMPARE_COLORS palette
- Colors: indigo, violet, pink, amber, emerald, blue, red, cyan
- External legend rendered above chart (colored dots + ticker names)

## Confidence Band Technique
TradingView has no native fill-between. Use two AreaSeries:
1. Upper: `topColor = "rgba(green, 0.15)"`, `bottomColor = "transparent"`, `lineColor = "transparent"`
2. Lower: `topColor = bg` (white/dark), `bottomColor = "transparent"`, `lineColor = "transparent"`
The lower series "erases" the fill below the lower bound.
Note: `lineWidth: 0` is invalid (type `LineWidth` requires ≥1). Use `lineWidth: 1` with `lineColor: "transparent"`.

## Key API Patterns
```typescript
import { createChart, LineSeries, AreaSeries, HistogramSeries } from "lightweight-charts";
const chart = createChart(container, options);
chart.addSeries(LineSeries, { color, lineWidth, lineStyle: 2 /* dashed */ });
chart.timeScale().fitContent();
chart.subscribeCrosshairMove(handler);
```

## Common Gotchas
- `lineWidth` only accepts integers (1, 2, 3, 4) — not 0 or 1.5
- Time values: strings "YYYY-MM-DD" cast as `Time` type
- `lineStyle: 2` = dashed, `lineStyle: undefined` = solid
- `ResizeObserver` needed for responsive width
- Dark mode: pass `isDark` prop → bg "#111827"/"#ffffff", text/grid colors
- Cleanup: `chart.remove()` on unmount + `unsubscribeCrosshairMove`
- Crosshair data lookup: use `useRef(Map)` for O(1) lookups, avoid re-renders

## What Still Uses Plotly
- Insights page only: sectors bar chart, correlation heatmap, quarterly chart
- These use `PlotlyChart.tsx` wrapper with `plotly.js-basic-dist`

## Package
`lightweight-charts: ^5.1.0` in `frontend/package.json`
