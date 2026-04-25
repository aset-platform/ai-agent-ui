# `<Suspense fallback={null}>` over `useSearchParams` blanks SSR

## Symptom

A page renders empty HTML during SSR (`curl -s
http://localhost:3000/admin` returns layout chrome but no page body).
Lighthouse LCP on every tab variant of the route is ~5 s with low TBT.
Hydration takes 3–4 s before any content appears.

## Root cause

Next 16 forces any subtree that calls `useSearchParams()` to render
client-only. The standard pattern:

```tsx
"use client";
function AdminPageInner() {
  const params = useSearchParams();
  // ...
}

export default function AdminPage() {
  return (
    <Suspense fallback={null}>
      <AdminPageInner />
    </Suspense>
  );
}
```

`fallback={null}` means the SSR'd HTML for that subtree is *literally
empty*. The browser receives a layout shell with no page body. Hydration
must complete before content paints, which on emulated mobile + CPU
throttling = 3–4 s of nothing visible.

## Fix

Replace `null` with a static page-chrome skeleton that:

1. Ships text in the initial HTML (so it's an LCP candidate at FCP).
2. **Mirrors the inner subtree's outer wrapper exactly** — same
   `space-y-*`, same `p-*`, same `min-h-*` content reserve — so the
   fallback → real swap doesn't shift layout (CLS budget ≤ 0.02 per
   CLAUDE.md §5.15).

```tsx
function AdminPageSkeleton() {
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <h1 className="text-2xl font-semibold ...">
        Admin Console
      </h1>
      <div className="min-h-[600px]" aria-hidden />
    </div>
  );
}

export default function AdminPage() {
  return (
    <Suspense fallback={<AdminPageSkeleton />}>
      <AdminPageInner />
    </Suspense>
  );
}
```

## Verify

```bash
JAR=$(mktemp)
curl -s -c $JAR -X POST http://localhost:3000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.com","password":"Admin123!"}' >/dev/null
curl -s -b $JAR http://localhost:3000/admin | grep -oE '<h1[^>]*>[^<]+'
# → <h1 ...>Admin Console
```

If you see the heading text in the SSR response, the fallback is
shipping. Lighthouse LCP should drop to ~1.5 s on every tab variant
(measured at FCP because the static h1 is the LCP candidate).

## Watch out for

- **Static heading must dominate the page.** If the data-bound content
  has elements larger than the h1 in viewport area (e.g., 296×45 px
  table cell vs 115×32 px `text-2xl` h1), Lighthouse picks the cell as
  LCP and the win is lost. Either bump the h1 size or accept that
  data-bound LCP is the ceiling for that route (Piotroski with long
  stock names, ScreenQL with 4-row textarea — both ~3-5 s and waiting
  on RSC pre-fetch).
- **Dimensions must match.** Iter2 of the Sprint 8 LCP iteration shipped
  a too-short fallback (`<h1> + <p>` ≈ 60 px tall) under a real layout
  that was 440+ px tall — content shifted down on swap, CLS spiked from
  0.000 to 0.254. Iter3 fix: reserve `min-h-[400px]` (insights) and
  `min-h-[600px]` (admin) inside the fallback to match the inner
  wrapper's existing min-height.
- **Set the fallback `aria-hidden`** so screen readers don't read the
  reserve div as content.

## Reference

Sprint 8 LCP iteration (commit b1c816e, 2026-04-25). Lifted 14 admin
tabs and 8 insights tabs from ~5 s LCP to ~1.5 s LCP each. The
600-px / 400-px content reserve numbers come from the existing
`min-h-[600px]` (admin/page.tsx:2154) and `min-h-[400px]`
(insights/page.tsx:2680) in the inner wrappers — match them exactly,
do not eyeball.
