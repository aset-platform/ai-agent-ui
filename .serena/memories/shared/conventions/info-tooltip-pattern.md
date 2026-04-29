# InfoTooltip — KPI / metric explainer popover

Reusable component at `frontend/components/common/InfoTooltip.tsx`. Renders a small ⓘ icon next to a label; hover/focus/click opens a popover with rich JSX content (What / How / Formula sections).

## When to use

- KPI cards / stat tiles where the metric definition isn't obvious from the label.
- Anywhere native HTML `title=""` is too cramped (no multi-line, disappears on cursor jitter, no styling).
- Whenever you'd otherwise need a footnote or a docs link to explain a number.

## When NOT to use

- For inline tooltips on *every* element — adds visual noise. Reserve for KPI tiles, complex metrics, computed aggregates.
- For long-form documentation. Keep the popover content scannable (~5 lines, formula included).

## API

```tsx
import { InfoTooltip } from "@/components/common/InfoTooltip";

<InfoTooltip>
  <p className="font-semibold mb-1">What</p>
  <p className="mb-2">Plain-English definition.</p>
  <p className="font-semibold mb-1">How</p>
  <p>The data path / formula.</p>
</InfoTooltip>
```

Props (all optional):

| Prop | Default | Use |
|---|---|---|
| `widthClass` | `"w-72"` (288px) | Override popover width — caller pins placement when changing this dramatically. |
| `placement` | `"auto"` | `"start"` (popover anchored at trigger's left, flows right), `"center"` (over trigger), `"end"` (anchored at right, flows left), or `"auto"` (default) which measures `getBoundingClientRect()` on open and picks the side that fits. |
| `label` | `"Show metric definition"` | aria-label override for the trigger button. |

## Auto-placement rule

Default `placement="auto"`. On open, reads the trigger's bounding rect and:

```
clearLeft  = rect.left
clearRight = window.innerWidth - rect.right

if (clearLeft  >= POPOVER_WIDTH_PX  &&  clearRight >= POPOVER_WIDTH_PX) → "center"
else if (clearRight >= clearLeft) → "start"  (anchor left, flow right)
else                              → "end"    (anchor right, flow left)
```

The strict centre rule (full popover width on each side) is *intentional* — a permissive `12px`-clearance threshold left the popover visually under the sidebar even though it was inside the viewport. See debugging history in `session/2026-04-29-recommendation-performance`.

The state update is deferred via `Promise.resolve().then` to satisfy `react-hooks/set-state-in-effect`.

## Integration with KPI cards

Both `RecommendationHistoryTab.tsx::KpiCard` and `RecommendationPerformanceTab.tsx::KpiTile` accept an optional `info: ReactNode` prop alongside the legacy `tooltip: string` prop. When `info` is set, the component renders the ⓘ next to the label and suppresses the native `title=""` (avoids double-tooltip glitch).

```tsx
function KpiCard({ label, value, info, tooltip }) {
  return (
    <div title={info ? undefined : tooltip}>
      <span className="inline-flex items-center ...">
        {label}
        {info && <InfoTooltip>{info}</InfoTooltip>}
      </span>
      <span>{value}</span>
    </div>
  );
}
```

## Content style guide

Every tooltip should follow the **What → How → Formula** pattern:

- **What** — one-sentence definition, scannable.
- **How** — the data path that feeds the metric. Reference real fields (`acted_on_date`, `excess_return_pct`).
- **Formula** — the exact arithmetic (`hits ÷ recs with N-day outcome × 100`).
- Optional **Heads up** in amber for known caveats (`benchmark_return_pct = 0` until wired to a real index).

Use `<span className="font-mono text-[11px]">` for code snippets inside the popover. Use `&apos;` not `'` to satisfy `react/no-unescaped-entities`.

## Accessibility

- Trigger is a real `<button>` with aria-label.
- Popover content is announced via `role="tooltip"` linked by `aria-describedby` when open.
- Opens on hover, keyboard focus, AND click — the click toggle keeps it usable on touch / mobile.

## See also

- `shared/architecture/recommendation-performance-tab` — current consumer.
- `shared/conventions/render-tooltip-helper` — for raw-DOM chart tooltips (different problem).
