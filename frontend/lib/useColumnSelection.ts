"use client";
/**
 * localStorage-backed column selection hook.
 *
 * Returns `[selected, setSelected, reset]`. Selected
 * is a stable array of field keys. Tolerates stale
 * keys from older versions of the catalog — filters
 * against `validKeys` on load.
 *
 * Used by the Screener + ScreenQL column selector
 * (ASETPLTFRM-333). Each tab has its own storage key
 * so selections are independent.
 */

import {
  useCallback, useEffect, useState,
} from "react";

export function useColumnSelection(
  storageKey: string,
  defaults: string[],
  validKeys: string[],
): [
    string[],
    (next: string[]) => void,
    () => void,
  ] {
  const [selected, setSelectedState] = useState<
    string[]
  >(defaults);
  const [hydrated, setHydrated] = useState(false);

  // Load persisted selection once after mount to avoid
  // SSR / client hydration mismatches.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(
        storageKey,
      );
      if (raw) {
        const parsed = JSON.parse(raw) as unknown;
        if (Array.isArray(parsed)) {
          const validSet = new Set(validKeys);
          const kept = parsed
            .filter(
              (v): v is string => typeof v === "string",
            )
            .filter((v) => validSet.has(v));
          if (kept.length > 0) {
            setSelectedState(kept);
          }
        }
      }
    } catch {
      // Fall back to defaults on parse error.
    }
    setHydrated(true);
  }, [storageKey, validKeys]);

  const setSelected = useCallback(
    (next: string[]) => {
      setSelectedState(next);
      if (typeof window !== "undefined") {
        try {
          window.localStorage.setItem(
            storageKey, JSON.stringify(next),
          );
        } catch {
          // Quota errors etc. — persist best-effort
        }
      }
    },
    [storageKey],
  );

  const reset = useCallback(() => {
    setSelectedState(defaults);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.removeItem(storageKey);
      } catch {
        // no-op
      }
    }
  }, [defaults, storageKey]);

  // Before hydration, render with defaults so SSR
  // markup matches initial client render.
  return [hydrated ? selected : defaults, setSelected, reset];
}
