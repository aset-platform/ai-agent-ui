"use client";

import type { DashboardData } from "@/hooks/useDashboardData";
import type { AnalysisResponse, SignalInfo } from "@/lib/types";
import { KpiTooltip } from "@/components/KpiTooltip";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { WidgetError } from "./WidgetError";

interface AnalysisSignalsWidgetProps {
  data: DashboardData<AnalysisResponse>;
}

function signalBadge(signal: string) {
  const s = signal.toLowerCase();
  if (s === "bullish") {
    return (
      <span
        className="
          inline-flex items-center px-2 py-0.5 rounded-full
          text-xs font-medium
          bg-emerald-100 text-emerald-700
          dark:bg-emerald-900/30 dark:text-emerald-400
        "
      >
        Bullish
      </span>
    );
  }
  if (s === "bearish") {
    return (
      <span
        className="
          inline-flex items-center px-2 py-0.5 rounded-full
          text-xs font-medium
          bg-red-100 text-red-700
          dark:bg-red-900/30 dark:text-red-400
        "
      >
        Bearish
      </span>
    );
  }
  return (
    <span
      className="
        inline-flex items-center px-2 py-0.5 rounded-full
        text-xs font-medium
        bg-amber-100 text-amber-700
        dark:bg-amber-900/30 dark:text-amber-400
      "
    >
      Neutral
    </span>
  );
}

function SignalRow({ signal }: { signal: SignalInfo }) {
  return (
    <div
      className="
        flex items-center justify-between gap-3
        py-3 border-b border-gray-100
        dark:border-gray-700/50 last:border-b-0
      "
    >
      <div className="flex-1 min-w-0">
        <p className="relative text-sm font-semibold text-gray-900 dark:text-gray-100">
          <KpiTooltip label={signal.name} />
        </p>
        <p
          className="font-mono text-sm text-gray-500 dark:text-gray-400 mt-0.5"
        >
          {signal.value}
        </p>
      </div>
      <div className="shrink-0">
        {signalBadge(signal.description || signal.signal)}
      </div>
    </div>
  );
}

function formatPct(value: number | null): string {
  if (value === null) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatDecimal(value: number | null): string {
  if (value === null) return "--";
  return value.toFixed(3);
}

export function AnalysisSignalsWidget({
  data,
}: AnalysisSignalsWidgetProps) {
  if (data.loading) {
    return <WidgetSkeleton className="h-72" />;
  }

  if (data.error) {
    return <WidgetError message={data.error} />;
  }

  const analyses = data.value?.analyses ?? [];

  return (
    <div
      className="
        rounded-xl border border-gray-200
        dark:border-gray-700
        bg-white dark:bg-gray-900
        shadow-sm
      "
    >
      {/* Header */}
      <div
        className="
          px-5 py-4 border-b border-gray-100
          dark:border-gray-800
        "
      >
        <h3
          className="
            text-base font-semibold text-gray-900
            dark:text-gray-100
          "
        >
          Analysis Signals
        </h3>
      </div>

      {/* Body */}
      <div className="px-5 py-4">
        {analyses.length === 0 ? (
          <p
            className="
              text-sm text-gray-500 dark:text-gray-400
              text-center py-8
            "
          >
            No analysis data yet
          </p>
        ) : (
          (() => {
            const a = analyses[0];
            return (
              <div>
                {/* Ticker badge */}
                <div className="mb-3">
                  <span
                    className="
                      inline-flex items-center px-2.5 py-1
                      rounded-md text-xs font-bold
                      tracking-wider uppercase
                      bg-gray-100 text-gray-800
                      dark:bg-gray-800 dark:text-gray-200
                    "
                  >
                    {a.ticker}
                  </span>
                  <span
                    className="
                      ml-2 text-xs text-gray-400
                      dark:text-gray-500
                    "
                  >
                    {a.analysis_date}
                  </span>
                </div>

                {/* Signal rows */}
                <div className="mb-4">
                  {a.signals.map((sig, i) => (
                    <SignalRow
                      key={`${a.ticker}-${sig.name}-${i}`}
                      signal={sig}
                    />
                  ))}
                </div>

                {/* Risk metrics */}
                <div
                  className="
                    grid grid-cols-2 sm:grid-cols-4 gap-3
                    p-3 rounded-lg
                    bg-gray-50 dark:bg-gray-800/50
                  "
                >
                  <div className="relative">
                    <p
                      className="
                        text-xs text-gray-500
                        dark:text-gray-400
                      "
                    >
                      <KpiTooltip label="Sharpe Ratio" />
                    </p>
                    <p
                      className="
                        font-mono text-sm font-medium
                        text-gray-900 dark:text-gray-100
                      "
                    >
                      {formatDecimal(a.sharpe_ratio)}
                    </p>
                  </div>
                  <div className="relative">
                    <p
                      className="
                        text-xs text-gray-500
                        dark:text-gray-400
                      "
                    >
                      <KpiTooltip label="Ann. Return" />
                    </p>
                    <p
                      className="
                        font-mono text-sm font-medium
                        text-gray-900 dark:text-gray-100
                      "
                    >
                      {formatPct(
                        a.annualized_return_pct,
                      )}
                    </p>
                  </div>
                  <div className="relative">
                    <p
                      className="
                        text-xs text-gray-500
                        dark:text-gray-400
                      "
                    >
                      <KpiTooltip label="Volatility" />
                    </p>
                    <p
                      className="
                        font-mono text-sm font-medium
                        text-gray-900 dark:text-gray-100
                      "
                    >
                      {formatPct(
                        a.annualized_volatility_pct,
                      )}
                    </p>
                  </div>
                  <div className="relative">
                    <p
                      className="
                        text-xs text-gray-500
                        dark:text-gray-400
                      "
                    >
                      <KpiTooltip label="Max Drawdown" />
                    </p>
                    <p
                      className="
                        font-mono text-sm font-medium
                        text-red-600 dark:text-red-400
                      "
                    >
                      {formatPct(a.max_drawdown_pct)}
                    </p>
                  </div>
                </div>
              </div>
            );
          })()
        )}
      </div>
    </div>
  );
}
