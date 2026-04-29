"use client";
/**
 * Small ⓘ icon with a hover/focus-triggered popover.
 *
 * Used next to KPI labels and other concise headings
 * where the metric needs a longer "what + how"
 * explanation that doesn't fit inline. Native HTML
 * ``title`` attribute is too cramped for multi-line
 * formula text and disappears on slight cursor
 * movement, so we ship a controlled popover instead.
 *
 * Accessibility:
 * - Trigger is a real <button> with aria-label.
 * - Popover content is announced via role="tooltip".
 * - Opens on hover *and* keyboard focus.
 *
 * Positioning:
 * - Default = below the icon, horizontally centred.
 * - ``placement="left"`` flushes the popover to the
 *   left edge of the trigger so labels near a card's
 *   right edge don't clip off-screen.
 */

import {
  useId, useState, type ReactNode,
} from "react";

interface InfoTooltipProps {
  /** Popover body — JSX so callers can format it. */
  children: ReactNode;
  /** Override the default 18rem popover width. */
  widthClass?: string;
  /** ``"center"`` (default) or ``"left"``. */
  placement?: "center" | "left";
  /** Optional aria-label override. */
  label?: string;
}

export function InfoTooltip({
  children,
  widthClass = "w-72",
  placement = "center",
  label = "Show metric definition",
}: InfoTooltipProps) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <span
      className="relative inline-flex items-center"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((v) => !v)}
        className={
          "ml-1 inline-flex h-4 w-4 items-center " +
          "justify-center rounded-full border " +
          "border-gray-400 dark:border-gray-500 " +
          "text-[10px] font-semibold text-gray-500 " +
          "dark:text-gray-400 cursor-help " +
          "hover:border-gray-600 hover:text-gray-700 " +
          "dark:hover:border-gray-300 " +
          "dark:hover:text-gray-200 " +
          "focus:outline-none focus:ring-2 " +
          "focus:ring-indigo-400"
        }
      >
        i
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className={
            "absolute top-full z-50 mt-1 " +
            "rounded-md border border-gray-200 " +
            "dark:border-gray-700 bg-white " +
            "dark:bg-gray-800 px-3 py-2 text-xs " +
            "leading-relaxed text-gray-700 " +
            "dark:text-gray-200 shadow-lg " +
            "normal-case tracking-normal font-normal " +
            (placement === "left"
              ? "left-0"
              : "left-1/2 -translate-x-1/2") +
            " " + widthClass
          }
        >
          {children}
        </span>
      )}
    </span>
  );
}
