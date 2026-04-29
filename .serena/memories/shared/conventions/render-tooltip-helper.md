# renderTooltip — DOM-construction helper for chart tooltips

`frontend/lib/renderTooltip.ts` — replacement for `el.innerHTML = ...` patterns inside chart-library tooltip callbacks (TradingView Lightweight Charts, ECharts, lightweight-charts crosshair, etc.).

## When to use

When a chart library hands you a raw `HTMLElement` ref and expects you to populate it imperatively. Examples in the codebase: StockChart crosshair, ForecastChart, Portfolio P&L chart, Portfolio Forecast chart — all in `frontend/app/(authenticated)/analytics/analysis/page.tsx`.

## Why not innerHTML

The eslint security rule flags `innerHTML` as an XSS risk. Even when interpolated values are numeric (`toFixed`, `toLocaleString`) or controlled date strings, the *pattern* is fragile — any future change that lets an unsanitised value flow in becomes silently exploitable. Plus the warnings drown out real issues on every commit.

## API

```ts
import {
  renderTooltip,
  type TooltipSegment,
} from "@/lib/renderTooltip";

renderTooltip(el, [
  { text: "April 18", className: "text-gray-500" },
  { text: "$214.50", className: "font-semibold" },
  { text: "+2.31%", className: "text-emerald-600" },
]);
```

Each segment is `{ text, className?, style?, leadingSpace? }`. Default behaviour:
- Spaces inserted between segments (override per-segment via `leadingSpace`).
- `text` rendered via `textContent` — no HTML parsing path.
- `style` applied via `Object.assign(span.style, ...)` — for cases that need an inline colour swatch (`{ background: ov.color }` for chart-series indicators).

## Conversion pattern

Old:
```ts
el.innerHTML =
  `<span class="text-gray-500">${date}</span> ` +
  `<span class="font-semibold">${sym}${price.toFixed(2)}</span> ` +
  `<span class="${pos ? "text-green" : "text-red"}">` +
  `${pos ? "+" : ""}${pct.toFixed(2)}%</span>`;
```

New:
```ts
renderTooltip(el, [
  { text: date, className: "text-gray-500" },
  { text: `${sym}${price.toFixed(2)}`, className: "font-semibold" },
  {
    text: `${pos ? "+" : ""}${pct.toFixed(2)}%`,
    className: pos ? "text-green" : "text-red",
  },
]);
```

For conditional segments (forecast badge, optional confidence band), build the array imperatively with `.push()`. For repeating overlays (chart series legend), iterate.

## Inline colour swatch (chart series indicator)

```ts
segments.push({
  text: "",
  className: "inline-block w-2 h-2 rounded-full mr-0.5",
  style: { background: ov.color },
});
```

Empty `text` is fine — `textContent = ""` produces an empty span, the swatch is purely visual via Tailwind w/h + inline background.

## What it does NOT do

- Does not parse HTML — by design.
- Does not handle nested elements within a segment — keep segments flat. If you need nesting, write the DOM construction inline; this helper is for the common single-line tooltip case.
- Does not memoise. Chart callbacks fire per crosshair move; the DOM ops are cheap (clear children + appendChild loop on ~5 spans). No measurable cost vs the prior `innerHTML` assignment.

## Implementation note

Children are cleared with a `while (el.firstChild) el.removeChild(...)` loop rather than `textContent = ""` or `innerHTML = ""`. Both alternatives technically work but the explicit loop avoids any future eslint-security rule re-flagging the file.

## Migration completed

Sites converted on 2026-04-29 (commit `df1aa2f`):
- `analytics/analysis/page.tsx:227` — StockChart crosshair tooltip
- `analytics/analysis/page.tsx:643` — ForecastChart tooltip
- `analytics/analysis/page.tsx:1220` — Portfolio P&L tooltip
- `analytics/analysis/page.tsx:1626` — Portfolio Forecast tooltip

Result: zero `innerHTML` usages remain in the file. eslint-security warnings cleared.

## See also

- `shared/conventions/info-tooltip-pattern` — for KPI label popovers (different problem; uses a controlled React component, not raw DOM).
