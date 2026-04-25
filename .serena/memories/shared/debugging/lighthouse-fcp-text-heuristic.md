# Lighthouse FCP heuristic — text/image/SVG only

Lighthouse's First Contentful Paint counter only fires on **text nodes, `<img>`, or `<svg>` content**. Pure-CSS `<div>` elements — including loading spinners built from `border` + `animate-spin` classes — do not register as FCP.

## Measured incident (2026-04-24, ASETPLTFRM-331)

Sprint 7's `AuthenticatedLayout` returned a fallback that was just:

```tsx
<div className="flex h-screen items-center justify-center bg-gray-50 ...">
  <div className="w-8 h-8 border-2 border-indigo-500 ... rounded-full animate-spin" />
</div>
```

Both the outer coloured div and the inner animated spinner are pure CSS (no text, no `<img>`, no `<svg>`). Lighthouse ignored them entirely and only fired FCP after React hydrated and the first real widget rendered — ~3 450 ms uniform across every authenticated route.

Replacing the fallback with an identical-looking skeleton that includes one text node:

```tsx
<main className="flex-1 flex items-center justify-center gap-3">
  <div className="w-6 h-6 border-2 border-indigo-500 ... animate-spin" />
  <span className="text-sm text-gray-500">Loading…</span>
</main>
```

…dropped FCP to **~1 515 ms** (−56%) on every authenticated route. One sibling text node, measured in a 34-route audit, was the difference between 3 450 ms and 1 515 ms FCP.

## Why

FCP fires on the first paint that includes "contentful" DOM. Chromium's `PerformancePaintTiming` implementation checks the paint for specific node types. Decorative borders, gradients, background colours, and CSS-animated transforms are considered "non-contentful" because they provide no semantic content to the user.

## How to apply

Any SSR skeleton / loading shell MUST include at least one of:
- A text node (even a single character)
- An `<img>` (a small brand logo works)
- An inline `<svg>` with drawn content

Bonus: text nodes also give Lighthouse a target to call LCP on via the Sprint 8 POC pattern (SSR the layout shell → layout-shaped skeleton with brand text → LCP fires on the skeleton, not on the hydrated widget).

## Related

- `shared/architecture/auth-layout-ssr-unlock` — follow-on: the Sprint 8 mount-gate removal depended on this FCP fix already being in place.
- Lighthouse's `first-contentful-paint` audit docs are silent on this — the algorithm lives in Chromium's `PaintTimingDetector`.
