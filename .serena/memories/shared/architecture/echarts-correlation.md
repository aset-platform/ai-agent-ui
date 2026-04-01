# ECharts Correlation Heatmap

## Why ECharts (not Plotly)
- `plotly.js-basic-dist` (1.1MB, current) does NOT include heatmap trace type
- `plotly.js-dist-min` (3.5MB) would add +2.4MB to bundle
- ECharts tree-shaken to ~150KB (HeatmapChart + Grid + Tooltip + VisualMap + CanvasRenderer)

## Component
`frontend/components/charts/CorrelationHeatmap.tsx`
- Tree-shaken ECharts imports via `echarts/core`
- Dynamic import via `echarts-for-react` (SSR-safe)
- Props: `tickers: string[]`, `matrix: number[][]`, `title?`, `height?`
- Features: correlation scores in each cell, Red→White→Blue colorscale,
  rounded cell borders, hover tooltips, visual map legend, dark/light mode
- Font size auto-scales based on ticker count (12px ≤6, 10px ≤10, 9px >10)

## Data Source
- Backend: `/insights/correlation?source=portfolio` (default)
- Added `source` parameter: "portfolio" (from holdings) or "watchlist" (legacy)
- Only portfolio stocks shown by default — cleaner chart with fewer tickers

## Jira: ASETPLTFRM-209
