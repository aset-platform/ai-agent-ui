/**
 * StockAnalysisLink — single source of truth for the stock-analysis
 * deep-link URL scheme, the chart-icon glyph, and the new-tab a11y /
 * security attributes (target="_blank" + rel="noopener noreferrer").
 *
 * Both AdvancedAnalyticsTable and SwingSetupsTab import from here so
 * URL shape and icon are guaranteed to stay in sync.
 */

/** Returns the deep-link URL for a ticker's analysis tab. */
export function stockAnalysisUrl(ticker: string): string {
  return (
    `/analytics/analysis?ticker=${encodeURIComponent(ticker)}` +
    `&tab=analysis`
  );
}

/** Sparkline/trend chart icon (16×16, stroke-only). Module-private. */
function ChartIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5 flex-shrink-0"
      aria-hidden="true"
    >
      <polyline points="1,12 5,7 8,9 12,4 15,6" />
      <polyline points="12,4 15,4 15,7" />
    </svg>
  );
}

interface StockAnalysisLinkProps {
  /** Ticker symbol, e.g. "INFY.NS". Used in href and default testId. */
  ticker: string;
  /**
   * Optional data-testid override.
   * Defaults to `stock-analysis-link-<ticker>`.
   */
  testId?: string;
}

/**
 * Renders a new-tab anchor linking to the stock analysis page for
 * `ticker`, with the chart icon and appropriate a11y attributes.
 */
export function StockAnalysisLink({
  ticker,
  testId,
}: StockAnalysisLinkProps) {
  return (
    <a
      href={stockAnalysisUrl(ticker)}
      target="_blank"
      rel="noopener noreferrer"
      title="Open stock analysis chart"
      aria-label={`Open stock analysis for ${ticker}`}
      data-testid={testId ?? `stock-analysis-link-${ticker}`}
      className={
        "text-indigo-500 hover:text-indigo-700 " +
        "dark:text-indigo-400 dark:hover:text-indigo-300 " +
        "transition-colors"
      }
    >
      <ChartIcon />
    </a>
  );
}
