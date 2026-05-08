"use client";
/**
 * URL ↔ state for AA bundle filters.
 *
 * Reads ``?tech=`` and ``?fund=`` on mount; writes via
 * ``router.replace()`` debounced 300 ms so checkbox spam doesn't
 * thrash navigation. Always emits sorted CSV so equivalent combos
 * (``a,b`` vs ``b,a``) hit the same SWR cache slot.
 *
 * Unknown keys arriving from a stale shared link are silently
 * dropped — the page renders with whatever the user can act on.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import {
  FUND_KEY_SET,
  TECH_KEY_SET,
} from "@/components/advanced-analytics/filterCatalogs";

const DEBOUNCE_MS = 300;

function parseCsv(raw: string | null, allowed: Set<string>): string[] {
  if (!raw) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const tok of raw.split(",")) {
    const t = tok.trim();
    if (!t || seen.has(t) || !allowed.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out.sort();
}

interface UseFilterParamsResult {
  tech: string[];
  fund: string[];
  setTech: (next: string[]) => void;
  setFund: (next: string[]) => void;
  resetAll: () => void;
}

export function useFilterParams(): UseFilterParamsResult {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const initial = useMemo(
    () => ({
      tech: parseCsv(sp.get("tech"), TECH_KEY_SET),
      fund: parseCsv(sp.get("fund"), FUND_KEY_SET),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [tech, setTechState] = useState<string[]>(initial.tech);
  const [fund, setFundState] = useState<string[]>(initial.fund);
  const techRef = useRef<string[]>(initial.tech);
  const fundRef = useRef<string[]>(initial.fund);
  const timerRef = useRef<number | null>(null);

  const flushToUrl = useCallback(
    (resetPage: boolean) => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        const params = new URLSearchParams(sp.toString());
        const nextTech = techRef.current;
        const nextFund = fundRef.current;
        if (nextTech.length > 0) {
          params.set("tech", nextTech.join(","));
        } else {
          params.delete("tech");
        }
        if (nextFund.length > 0) {
          params.set("fund", nextFund.join(","));
        } else {
          params.delete("fund");
        }
        if (resetPage) params.set("page", "1");
        const qs = params.toString();
        router.replace(qs ? `${pathname}?${qs}` : pathname, {
          scroll: false,
        });
      }, DEBOUNCE_MS);
    },
    [pathname, router, sp],
  );

  const setTech = useCallback(
    (next: string[]) => {
      const sorted = [...next].sort();
      techRef.current = sorted;
      setTechState(sorted);
      flushToUrl(true);
    },
    [flushToUrl],
  );
  const setFund = useCallback(
    (next: string[]) => {
      const sorted = [...next].sort();
      fundRef.current = sorted;
      setFundState(sorted);
      flushToUrl(true);
    },
    [flushToUrl],
  );
  const resetAll = useCallback(() => {
    techRef.current = [];
    fundRef.current = [];
    setTechState([]);
    setFundState([]);
    flushToUrl(false);
  }, [flushToUrl]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  return { tech, fund, setTech, setFund, resetAll };
}
