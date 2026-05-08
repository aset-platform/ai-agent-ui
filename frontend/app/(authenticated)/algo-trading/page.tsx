// frontend/app/(authenticated)/algo-trading/page.tsx
/**
 * Algo Trading route — RSC wrapper. Mirrors the
 * /advanced-analytics shell (§5.3 cookie-auth-rsc-pattern):
 * <Suspense fallback={<h1>}> ensures the SSR HTML always
 * carries an LCP candidate even though useSearchParams
 * forces the inner subtree client-only.
 *
 * Hard 403 for general users is enforced by the backend
 * `pro_or_superuser` guard (lands in Slice 2). The nav-gate
 * already hides the menu for ineligible users (Task 4).
 */

import { Suspense } from "react";

import AlgoTradingClient from "./AlgoTradingClient";

export const dynamic = "force-dynamic";

export default function AlgoTradingPage() {
  return (
    <Suspense fallback={<AlgoTradingFallback />}>
      <AlgoTradingClient />
    </Suspense>
  );
}

function AlgoTradingFallback() {
  return (
    <div className="space-y-4 p-6 min-h-[600px]">
      <h1 className="text-xl font-semibold">Algo Trading</h1>
    </div>
  );
}
