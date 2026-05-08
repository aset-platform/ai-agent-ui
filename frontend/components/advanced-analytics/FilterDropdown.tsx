"use client";
/**
 * Reusable popover for one bundle (Technical or Fundamentals)
 * on the Advanced Analytics tabs. Renders sections by
 * ``catalog[].section``; entries with a ``group`` field are
 * mutually-exclusive radios within that group.
 *
 * Pairs with ``useFilterParams`` for URL ↔ state. Toolbar
 * placement: between ticker_type select and ColumnSelector
 * in ``AdvancedAnalyticsTable``.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { FilterOption } from "./filterCatalogs";

interface FilterDropdownProps {
  bundleId: "tech" | "fund";
  bundleLabel: string;
  catalog: FilterOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  onReset: () => void;
}

export function FilterDropdown({
  bundleId,
  bundleLabel,
  catalog,
  selected,
  onChange,
  onReset,
}: FilterDropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const sections = useMemo(() => {
    const out: { name: string; items: FilterOption[] }[] = [];
    for (const opt of catalog) {
      const last = out[out.length - 1];
      if (last && last.name === opt.section) {
        last.items.push(opt);
      } else {
        out.push({ name: opt.section, items: [opt] });
      }
    }
    return out;
  }, [catalog]);

  const handleToggle = useCallback(
    (opt: FilterOption) => {
      if (opt.group) {
        const groupKeys = new Set(
          catalog
            .filter((o) => o.group === opt.group)
            .map((o) => o.key),
        );
        const remaining = selected.filter((k) => !groupKeys.has(k));
        if (selectedSet.has(opt.key)) {
          onChange(remaining);
        } else {
          onChange([...remaining, opt.key]);
        }
      } else {
        if (selectedSet.has(opt.key)) {
          onChange(selected.filter((k) => k !== opt.key));
        } else {
          onChange([...selected, opt.key]);
        }
      }
    },
    [catalog, onChange, selected, selectedSet],
  );

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={
          `Open ${bundleLabel} filters, ${selected.length} active`
        }
        data-testid={`aa-filter-${bundleId}-button`}
        className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-indigo-500 inline-flex items-center gap-1"
      >
        {bundleLabel}
        {selected.length > 0 && (
          <span className="rounded-full bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 px-1.5 text-[10px]">
            {selected.length}
          </span>
        )}
        <span className="text-[8px]">▾</span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={`${bundleLabel} filters`}
          data-testid={`aa-filter-${bundleId}-popover`}
          className="absolute right-0 z-30 mt-1 w-64 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg p-3 text-xs"
        >
          <div className="flex items-center justify-between mb-2">
            <span className="font-semibold text-gray-700 dark:text-gray-200">
              {bundleLabel}
            </span>
            <button
              type="button"
              onClick={onReset}
              data-testid={`aa-filter-${bundleId}-reset`}
              className="text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              Reset
            </button>
          </div>
          <div className="max-h-80 overflow-y-auto space-y-3">
            {sections.map((sec) => (
              <fieldset key={sec.name}>
                <legend className="text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">
                  {sec.name}
                </legend>
                {sec.items.map((opt) => {
                  const isRadio = Boolean(opt.group);
                  const checked = selectedSet.has(opt.key);
                  return (
                    <label
                      key={opt.key}
                      className="flex items-center gap-2 py-0.5 cursor-pointer hover:text-indigo-600 dark:hover:text-indigo-400"
                      title={opt.tooltip}
                    >
                      <input
                        type={isRadio ? "radio" : "checkbox"}
                        name={
                          isRadio
                            ? `${bundleId}-${opt.group}`
                            : undefined
                        }
                        checked={checked}
                        onChange={
                          isRadio ? undefined : () => handleToggle(opt)
                        }
                        onClick={
                          isRadio ? () => handleToggle(opt) : undefined
                        }
                        readOnly={isRadio}
                        data-testid={
                          `aa-filter-${bundleId}-option-${opt.key}`
                        }
                        className="cursor-pointer"
                      />
                      <span>{opt.label}</span>
                    </label>
                  );
                })}
              </fieldset>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
