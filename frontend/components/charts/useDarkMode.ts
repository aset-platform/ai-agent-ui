"use client";
/**
 * Detect dark mode from the DOM classList.
 *
 * Uses a MutationObserver on <html> to react to
 * theme toggles, avoiding SSR hydration mismatches
 * that occur with useTheme() hook alone.
 */

import { useState, useEffect } from "react";

export function useDomDark(isDark: boolean) {
  const [domDark, setDomDark] = useState(() =>
    typeof document !== "undefined"
      ? document.documentElement.classList.contains(
          "dark",
        )
      : isDark,
  );

  useEffect(() => {
    const el = document.documentElement;
    const update = () =>
      setDomDark(el.classList.contains("dark"));
    update();
    const obs = new MutationObserver(update);
    obs.observe(el, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, []);

  return isDark || domDark;
}
