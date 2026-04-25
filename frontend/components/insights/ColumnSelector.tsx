"use client";
/**
 * Column selector popover for Screener + ScreenQL
 * (ASETPLTFRM-333).
 *
 * Presents a grouped checkbox list of all catalog
 * fields. User checks/unchecks to toggle column
 * visibility. Selection persists via `localStorage`
 * through the parent-provided `selected` / `onChange`
 * props (pair with `useColumnSelection`).
 *
 * Expected placement: top-right of the table header,
 * next to `DownloadCsvButton`.
 */

import {
  useEffect, useMemo, useRef, useState,
} from "react";

export interface ColumnSpec {
  key: string;
  label: string;
  category: string;
}

interface Props {
  catalog: ColumnSpec[];
  selected: string[];
  onChange: (next: string[]) => void;
  onReset?: () => void;
  lockedKeys?: string[];
  /** Label override for the trigger button. */
  buttonLabel?: string;
}

export function ColumnSelector({
  catalog,
  selected,
  onChange,
  onReset,
  lockedKeys = [],
  buttonLabel = "Columns",
}: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const locked = useMemo(
    () => new Set(lockedKeys), [lockedKeys],
  );
  const selectedSet = useMemo(
    () => new Set(selected), [selected],
  );

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (!rootRef.current) return;
      if (
        !rootRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => {
      document.removeEventListener(
        "mousedown", handler,
      );
    };
  }, [open]);

  // Group by category while preserving catalog order.
  const grouped = useMemo(() => {
    const q = search.trim().toLowerCase();
    const groups = new Map<string, ColumnSpec[]>();
    for (const col of catalog) {
      if (
        q && !col.label.toLowerCase().includes(q)
        && !col.key.toLowerCase().includes(q)
      ) {
        continue;
      }
      const arr = groups.get(col.category) ?? [];
      arr.push(col);
      groups.set(col.category, arr);
    }
    return Array.from(groups.entries());
  }, [catalog, search]);

  const totalCount = catalog.length;
  const selectedCount = selected.length;

  function toggle(key: string) {
    if (locked.has(key)) return;
    const next = selectedSet.has(key)
      ? selected.filter((k) => k !== key)
      : [...selected, key];
    onChange(next);
  }

  function toggleCategory(category: string) {
    const keys = catalog
      .filter((c) => c.category === category)
      .map((c) => c.key);
    const allSelected = keys.every(
      (k) => selectedSet.has(k) || locked.has(k),
    );
    if (allSelected) {
      // Deselect all in category (except locked).
      const lockedKeep = keys.filter(
        (k) => locked.has(k),
      );
      const rest = selected.filter(
        (k) => !keys.includes(k)
          || lockedKeep.includes(k),
      );
      onChange(rest);
    } else {
      // Select everything in category.
      const union = new Set(selected);
      for (const k of keys) union.add(k);
      onChange(Array.from(union));
    }
  }

  function selectAll() {
    onChange(catalog.map((c) => c.key));
  }

  function deselectAll() {
    // Preserve locked keys when deselecting everything.
    onChange(Array.from(locked));
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="column-selector-trigger"
        className="inline-flex items-center gap-1.5
          rounded-md border border-gray-200
          dark:border-gray-700 bg-white
          dark:bg-gray-800 px-2.5 py-1.5 text-xs
          font-medium text-gray-700 dark:text-gray-300
          hover:bg-gray-50 dark:hover:bg-gray-700
          transition-colors"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="w-3.5 h-3.5"
          aria-hidden="true"
        >
          <path
            fillRule="evenodd"
            d="M3 5a2 2 0 012-2h10a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V5zm2 0v10h10V5H5zm2 2h6v2H7V7zm0 4h6v2H7v-2z"
            clipRule="evenodd"
          />
        </svg>
        {buttonLabel} ({selectedCount}/{totalCount})
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          className={`w-3 h-3 transition-transform ${
            open ? "rotate-180" : ""
          }`}
          aria-hidden="true"
        >
          <path
            fillRule="evenodd"
            d="M5.23 7.21a.75.75 0 011.06.02L10 11.06l3.71-3.83a.75.75 0 111.08 1.04l-4.25 4.39a.75.75 0 01-1.08 0L5.21 8.27a.75.75 0 01.02-1.06z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Column selector"
          data-testid="column-selector-popover"
          className="absolute right-0 top-full mt-1
            z-30 w-72 max-h-[480px] overflow-hidden
            rounded-lg border border-gray-200
            dark:border-gray-700 bg-white
            dark:bg-gray-900 shadow-xl flex flex-col"
        >
          {/* Header: search + bulk actions */}
          <div className="p-2 border-b
            border-gray-100 dark:border-gray-800
            space-y-2"
          >
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search fields..."
              className="w-full rounded-md border
                border-gray-200 dark:border-gray-700
                bg-white dark:bg-gray-800 px-2 py-1
                text-xs text-gray-800
                dark:text-gray-200
                placeholder:text-gray-400"
            />
            <div className="flex gap-1.5 text-[11px]">
              <button
                type="button"
                onClick={selectAll}
                className="flex-1 rounded
                  border border-gray-200
                  dark:border-gray-700 px-2 py-1
                  text-gray-700 dark:text-gray-300
                  hover:bg-gray-50
                  dark:hover:bg-gray-800"
              >
                Select all
              </button>
              <button
                type="button"
                onClick={deselectAll}
                className="flex-1 rounded
                  border border-gray-200
                  dark:border-gray-700 px-2 py-1
                  text-gray-700 dark:text-gray-300
                  hover:bg-gray-50
                  dark:hover:bg-gray-800"
              >
                Deselect all
              </button>
            </div>
          </div>

          {/* Body: grouped checkboxes */}
          <div className="flex-1 overflow-y-auto p-1">
            {grouped.length === 0 && (
              <p className="text-xs text-gray-500
                dark:text-gray-400 px-3 py-4 text-center"
              >
                No fields match &ldquo;{search}&rdquo;.
              </p>
            )}
            {grouped.map(([category, fields]) => {
              const catSelected = fields.filter(
                (f) => selectedSet.has(f.key)
                  || locked.has(f.key),
              ).length;
              return (
                <div key={category} className="mb-1">
                  <button
                    type="button"
                    onClick={() => toggleCategory(
                      category,
                    )}
                    className="w-full flex items-center
                      justify-between px-2 py-1.5
                      text-xs font-semibold
                      uppercase tracking-wide
                      text-gray-500
                      dark:text-gray-400
                      hover:bg-gray-50
                      dark:hover:bg-gray-800 rounded"
                  >
                    <span>{category}</span>
                    <span className="font-mono
                      text-[10px]"
                    >
                      {catSelected}/{fields.length}
                    </span>
                  </button>
                  {fields.map((f) => {
                    const isLocked = locked.has(f.key);
                    const isSelected = (
                      selectedSet.has(f.key) || isLocked
                    );
                    return (
                      <label
                        key={f.key}
                        className={`flex items-center
                          gap-2 px-3 py-1 text-xs
                          rounded ${
                            isLocked
                              ? "text-gray-400 dark:text-gray-500 cursor-not-allowed"
                              : "text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                          }`}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          disabled={isLocked}
                          onChange={() => toggle(f.key)}
                          data-testid={`col-toggle-${f.key}`}
                          className="w-3.5 h-3.5
                            accent-indigo-500"
                        />
                        <span className="flex-1
                          truncate"
                        >
                          {f.label}
                        </span>
                        {isLocked && (
                          <span className="text-[10px]
                            text-gray-400"
                          >
                            locked
                          </span>
                        )}
                      </label>
                    );
                  })}
                </div>
              );
            })}
          </div>

          {/* Footer: reset */}
          {onReset && (
            <div className="p-2 border-t
              border-gray-100 dark:border-gray-800"
            >
              <button
                type="button"
                onClick={onReset}
                className="w-full text-[11px]
                  text-indigo-600
                  dark:text-indigo-400 hover:underline"
              >
                Reset to defaults
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
