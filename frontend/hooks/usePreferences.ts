"use client";
/**
 * User preferences hook — localStorage + Redis sync.
 *
 * Reads from localStorage instantly on mount.  Falls back to
 * Redis (via GET /v1/users/me/preferences) on first device
 * login.  Writes to localStorage immediately and debounces
 * a PUT to Redis every 5 seconds.
 */

import {
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

export interface ChartPrefs {
  ticker?: string;
  tab?: string;
  indicators?: Record<string, boolean>;
  range?: string;
  interval?: string;
}

export interface UserPreferences {
  chart?: ChartPrefs;
  dashboard?: { marketFilter?: string };
  insights?: {
    activeTab?: string;
    market?: string;
    sector?: string;
  };
  admin?: { activeTab?: string };
  navigation?: { lastPage?: string };
  last_login?: string;
}

// ---------------------------------------------------------------
// Constants
// ---------------------------------------------------------------

const STORAGE_KEY = "user_prefs";
const SYNC_DEBOUNCE_MS = 5000;

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function readLocal(): UserPreferences | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeLocal(prefs: UserPreferences): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(prefs),
    );
  } catch {
    // localStorage full or blocked — ignore
  }
}

// ---------------------------------------------------------------
// Hook
// ---------------------------------------------------------------

export function usePreferences(): [
  UserPreferences,
  (
    section: keyof UserPreferences,
    values: Record<string, unknown>,
  ) => void,
] {
  const [prefs, setPrefs] =
    useState<UserPreferences>(
      () => readLocal() ?? {},
    );
  const syncTimer = useRef<ReturnType<
    typeof setTimeout
  > | null>(null);
  const pendingSync = useRef(false);

  // On mount: if localStorage is empty, fetch from
  // Redis (new device / cleared browser).
  useEffect(() => {
    const local = readLocal();
    if (local && Object.keys(local).length > 0) {
      return; // already have local prefs
    }
    apiFetch(`${API_URL}/users/me/preferences`)
      .then((r) => {
        if (!r.ok) return;
        return r.json();
      })
      .then((data: UserPreferences | undefined) => {
        if (data && Object.keys(data).length > 0) {
          setPrefs(data);
          writeLocal(data);
        }
      })
      .catch(() => {
        // Silently fail — use defaults
      });
  }, []);

  // Sync to Redis (debounced)
  const syncToRedis = useCallback(() => {
    if (syncTimer.current) {
      clearTimeout(syncTimer.current);
    }
    pendingSync.current = true;
    syncTimer.current = setTimeout(() => {
      const current = readLocal();
      if (!current) return;
      apiFetch(`${API_URL}/users/me/preferences`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(current),
      }).catch(() => {
        // Silently fail — local is source of truth
      });
      pendingSync.current = false;
    }, SYNC_DEBOUNCE_MS);
  }, []);

  // Flush on page unload
  useEffect(() => {
    const flush = () => {
      if (!pendingSync.current) return;
      const current = readLocal();
      if (!current) return;
      // Use sendBeacon for reliable unload sync
      const blob = new Blob(
        [JSON.stringify(current)],
        { type: "application/json" },
      );
      navigator.sendBeacon(
        `${API_URL}/users/me/preferences`,
        blob,
      );
    };
    window.addEventListener(
      "beforeunload",
      flush,
    );
    return () =>
      window.removeEventListener(
        "beforeunload",
        flush,
      );
  }, []);

  // Update a section — uses setTimeout(0) to defer
  // the state update so it never fires during render.
  const updatePrefs = useCallback(
    (
      section: keyof UserPreferences,
      values: Record<string, unknown>,
    ) => {
      setTimeout(() => {
        setPrefs((prev) => {
          const existing =
            (prev[section] as Record<
              string,
              unknown
            >) ?? {};
          const updated = {
            ...prev,
            [section]: { ...existing, ...values },
            last_login: new Date().toISOString(),
          };
          writeLocal(updated);
          return updated;
        });
        syncToRedis();
      }, 0);
    },
    [syncToRedis],
  );

  return [prefs, updatePrefs];
}
