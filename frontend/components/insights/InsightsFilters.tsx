"use client";
/**
 * Shared filter bar for Insights tabs.
 *
 * Provides market, sector, and ticker dropdowns.
 * Each filter is optional — pass the props you need.
 */

interface InsightsFiltersProps {
  /** Market filter. */
  market?: string;
  onMarketChange?: (v: string) => void;
  /** Sector filter (options come from API). */
  sector?: string;
  onSectorChange?: (v: string) => void;
  sectors?: string[];
  /** Ticker filter. */
  ticker?: string;
  onTickerChange?: (v: string) => void;
  tickers?: string[];
  /** RSI filter (screener only). */
  rsiFilter?: string;
  onRsiFilterChange?: (v: string) => void;
}

const selectClass = `
  rounded-lg border border-gray-300
  dark:border-gray-600 bg-white dark:bg-gray-800
  px-2.5 py-1.5 text-sm
  text-gray-700 dark:text-gray-200
  focus:outline-none focus:ring-2
  focus:ring-indigo-500/40
`;

export function InsightsFilters({
  market,
  onMarketChange,
  sector,
  onSectorChange,
  sectors = [],
  ticker,
  onTickerChange,
  tickers = [],
  rsiFilter,
  onRsiFilterChange,
}: InsightsFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Market */}
      {onMarketChange && (
        <select
          data-testid="insights-market-filter"
          value={market ?? "all"}
          onChange={(e) =>
            onMarketChange(e.target.value)
          }
          className={selectClass}
        >
          <option value="all">All Markets</option>
          <option value="india">India</option>
          <option value="us">US</option>
        </select>
      )}

      {/* Sector */}
      {onSectorChange && sectors.length > 0 && (
        <select
          data-testid="insights-sector-filter"
          value={sector ?? "all"}
          onChange={(e) =>
            onSectorChange(e.target.value)
          }
          className={selectClass}
        >
          <option value="all">All Sectors</option>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      )}

      {/* Ticker */}
      {onTickerChange && tickers.length > 0 && (
        <select
          data-testid="insights-ticker-filter"
          value={ticker ?? "all"}
          onChange={(e) =>
            onTickerChange(e.target.value)
          }
          className={selectClass}
        >
          <option value="all">All Tickers</option>
          {tickers.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      )}

      {/* RSI (screener only) */}
      {onRsiFilterChange && (
        <select
          data-testid="insights-rsi-filter"
          value={rsiFilter ?? "all"}
          onChange={(e) =>
            onRsiFilterChange(e.target.value)
          }
          className={selectClass}
        >
          <option value="all">All RSI</option>
          <option value="oversold">
            Oversold (&lt;30)
          </option>
          <option value="neutral">
            Neutral (30–70)
          </option>
          <option value="overbought">
            Overbought (&gt;70)
          </option>
        </select>
      )}
    </div>
  );
}
