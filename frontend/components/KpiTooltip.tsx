"use client";
/**
 * Inline tooltip for KPI labels.
 *
 * Renders the label text with a small info icon.
 * On hover, shows a brief explanation in a floating
 * tooltip below the icon.
 */

import { useState, useRef } from "react";

/** KPI explanations for financial metrics. */
export const KPI_TIPS: Record<string, string> = {
  "RSI":
    "Relative Strength Index (0\u2013100). " +
    "<30 = oversold, >70 = overbought.",
  "RSI 14":
    "14-day Relative Strength Index (0\u2013100). " +
    "<30 = oversold, >70 = overbought.",
  "RSI Signal":
    "RSI classification: Oversold (<30), " +
    "Neutral (30\u201370), Overbought (>70).",
  "MACD":
    "Moving Average Convergence Divergence. " +
    "Bullish when MACD crosses above signal line.",
  "SMA 50":
    "50-day Simple Moving Average. " +
    "Price above = short-term uptrend.",
  "SMA 200":
    "200-day Simple Moving Average. " +
    "Price above = long-term uptrend.",
  "vs SMA 200":
    "Current price relative to the 200-day " +
    "moving average. Above = bullish.",
  "Sharpe":
    "Risk-adjusted return. >1 = good, " +
    ">2 = very good, <0 = losing money.",
  "Sharpe Ratio":
    "Risk-adjusted return. >1 = good, " +
    ">2 = very good, <0 = losing money.",
  "Ann. Ret %":
    "Annualized return percentage \u2014 " +
    "compound annual growth rate.",
  "Ann. Return":
    "Annualized return percentage \u2014 " +
    "compound annual growth rate.",
  "Vol %":
    "Annualized volatility \u2014 standard " +
    "deviation of daily returns, annualized.",
  "Volatility":
    "Annualized volatility \u2014 standard " +
    "deviation of daily returns, annualized.",
  "Avg Vol %":
    "Average annualized volatility across " +
    "all stocks in the sector.",
  "Max DD %":
    "Maximum drawdown \u2014 largest peak-to-" +
    "trough decline in portfolio value.",
  "Max Drawdown":
    "Maximum drawdown \u2014 largest peak-to-" +
    "trough decline in portfolio value.",
  "DD Days":
    "Duration (in trading days) of the " +
    "longest drawdown period.",
  "Bull %":
    "Percentage of time the stock spent in " +
    "a bullish (uptrend) phase.",
  "Bear %":
    "Percentage of time the stock spent in " +
    "a bearish (downtrend) phase.",
  "Avg Ret %":
    "Average annualized return across " +
    "all stocks in the sector.",
  "Avg Sharpe":
    "Average Sharpe ratio across all " +
    "stocks in the sector.",
  "EPS":
    "Earnings Per Share \u2014 net income " +
    "divided by shares outstanding.",
};

interface KpiTooltipProps {
  label: string;
  tip?: string;
  className?: string;
}

export function KpiTooltip({
  label,
  tip,
  className = "",
}: KpiTooltipProps) {
  const [show, setShow] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const text = tip ?? KPI_TIPS[label];

  if (!text) {
    return <span className={className}>{label}</span>;
  }

  return (
    <span
      ref={ref}
      className={`inline-flex items-center gap-1 ${className}`}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {label}
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 20 20"
        fill="currentColor"
        className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 shrink-0"
      >
        <path
          fillRule="evenodd"
          d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z"
          clipRule="evenodd"
        />
      </svg>
      {show && (
        <span className="absolute z-50 mt-1 top-full left-0 w-52 px-2.5 py-1.5 rounded-lg bg-gray-900 dark:bg-gray-700 text-xs text-white shadow-lg leading-relaxed pointer-events-none">
          {text}
        </span>
      )}
    </span>
  );
}
