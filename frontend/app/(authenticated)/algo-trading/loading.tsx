// frontend/app/(authenticated)/algo-trading/loading.tsx
/**
 * Loading shell for /algo-trading. Includes text so
 * Lighthouse FCP fires (per CLAUDE.md §6.6).
 */

export default function Loading() {
  return (
    <div className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">Algo Trading</h1>
      <p className="text-sm text-gray-500">Loading…</p>
    </div>
  );
}
