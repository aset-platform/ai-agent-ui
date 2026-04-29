/**
 * Tooltip rendering helper for chart libraries that
 * give us a raw HTMLElement ref (TradingView Lightweight
 * Charts, ECharts custom tooltips, etc.).
 *
 * Builds child nodes via ``createElement`` /
 * ``textContent`` rather than ``innerHTML``, eliminating
 * the XSS surface entirely. Today the values flowing
 * into these tooltips are already safe (numeric or
 * server-controlled date strings), but a class of
 * eslint-security warnings flagged the pattern as a
 * smell and a future change could trivially leak user
 * input into the same code path.
 *
 * Usage:
 * ```ts
 * renderTooltip(el, [
 *   { text: data.date, className: "text-gray-500" },
 *   { text: `$${price.toFixed(2)}`,
 *     className: "font-semibold" },
 * ]);
 * ```
 */

export interface TooltipSegment {
  /** Visible text content; rendered via textContent. */
  text: string;
  /** Tailwind class string applied to the span. */
  className?: string;
  /**
   * Inline style overrides (e.g. ``{ background: col }``
   * for chart-series colour swatches). Properties are
   * assigned via ``Object.assign(span.style, ...)``;
   * style attributes can't carry executable content.
   */
  style?: Partial<CSSStyleDeclaration>;
  /**
   * Force-on or force-off the leading space.
   * Default: a space is inserted before every segment
   * after the first.
   */
  leadingSpace?: boolean;
}

export function renderTooltip(
  el: HTMLElement,
  segments: TooltipSegment[],
): void {
  // Clear children explicitly. Avoids both
  // ``innerHTML = ""`` (security-rule flag) and a
  // ``textContent = ""`` reflow flicker.
  while (el.firstChild) {
    el.removeChild(el.firstChild);
  }

  segments.forEach((seg, i) => {
    const lead = seg.leadingSpace ?? (i > 0);
    if (lead) {
      el.appendChild(
        document.createTextNode(" "),
      );
    }
    const span = document.createElement("span");
    if (seg.className) span.className = seg.className;
    if (seg.style) {
      Object.assign(span.style, seg.style);
    }
    span.textContent = seg.text;
    el.appendChild(span);
  });
}
