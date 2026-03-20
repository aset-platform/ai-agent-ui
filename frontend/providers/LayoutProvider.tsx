"use client";
/**
 * Layout context provider — manages sidebar collapsed state and
 * mobile menu visibility.
 *
 * Persists sidebar preference in localStorage. Auto-collapse logic
 * (when chat opens) is handled by the Sidebar component itself,
 * reading from ChatContext.
 */

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react";

interface LayoutContextValue {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  toggleSidebar: () => void;
  mobileMenuOpen: boolean;
  setMobileMenuOpen: (
    v: boolean | ((prev: boolean) => boolean),
  ) => void;
}

const LayoutContext =
  createContext<LayoutContextValue | null>(null);

const STORAGE_KEY = "sidebar_collapsed";

function getStoredCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(STORAGE_KEY) === "true";
}

export function LayoutProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [sidebarCollapsed, setSidebarCollapsedRaw] =
    useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] =
    useState(false);
  const hydrated = useRef(false);

  // Hydrate from localStorage once after mount.
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    /* eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot hydration from localStorage */
    setSidebarCollapsedRaw(getStoredCollapsed());
  }, []);

  const setSidebarCollapsed = useCallback(
    (v: boolean) => {
      setSidebarCollapsedRaw(v);
      localStorage.setItem(STORAGE_KEY, String(v));
    },
    [],
  );

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsedRaw((prev) => {
      const next = !prev;
      localStorage.setItem(STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  return (
    <LayoutContext.Provider
      value={{
        sidebarCollapsed,
        setSidebarCollapsed,
        toggleSidebar,
        mobileMenuOpen,
        setMobileMenuOpen,
      }}
    >
      {children}
    </LayoutContext.Provider>
  );
}

export function useLayoutContext(): LayoutContextValue {
  const ctx = useContext(LayoutContext);
  if (!ctx) {
    throw new Error(
      "useLayoutContext must be used within LayoutProvider",
    );
  }
  return ctx;
}
