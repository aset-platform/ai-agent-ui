"use client";

/**
 * Theme hook — manages light/dark/system preference.
 *
 * Reads from ``localStorage("theme")`` on mount, falls back to the
 * browser's ``prefers-color-scheme`` media query, and applies/removes
 * the ``dark`` class on ``<html>`` so Tailwind's ``dark:`` variants
 * activate correctly.
 */

import { useState, useEffect, useCallback } from "react";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "theme";

/** Resolve "system" to the actual light/dark value. */
function resolveSystemTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** Read the persisted preference, defaulting to "system". */
function getStoredTheme(): ThemeMode {
  if (typeof window === "undefined") return "system";
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") {
    return stored;
  }
  return "system";
}

/** Apply or remove the ``dark`` class on ``<html>``. */
function applyTheme(mode: ThemeMode): void {
  const resolved = mode === "system" ? resolveSystemTheme() : mode;
  const root = document.documentElement;
  if (resolved === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(getStoredTheme);

  // Apply on mount and when mode changes.
  useEffect(() => {
    applyTheme(mode);
    localStorage.setItem(STORAGE_KEY, mode);
  }, [mode]);

  // Listen for OS theme changes when mode is "system".
  useEffect(() => {
    if (mode !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => applyTheme("system");
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [mode]);

  const setTheme = useCallback((m: ThemeMode) => setMode(m), []);

  const toggle = useCallback(() => {
    setMode((prev) => {
      const resolved = prev === "system" ? resolveSystemTheme() : prev;
      return resolved === "dark" ? "light" : "dark";
    });
  }, []);

  const resolvedTheme: "light" | "dark" =
    mode === "system" ? resolveSystemTheme() : mode;

  return { mode, resolvedTheme, setTheme, toggle };
}
