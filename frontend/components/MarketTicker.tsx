"use client";
/**
 * Inline market ticker for Nifty 50 + Sensex.
 *
 * Polls GET /v1/market/indices every 30 seconds via apiFetch.
 * Hidden on mobile (< md breakpoint).
 */

import { useEffect, useState, useCallback } from "react";
import { apiFetch } from "@/lib/apiFetch";

interface IndexData {
  price: number;
  change: number;
  change_pct: number;
  prev_close: number;
  open: number;
  high: number;
  low: number;
}

interface MarketIndices {
  nifty: IndexData;
  sensex: IndexData;
  market_state: string;
  timestamp: string;
  stale: boolean;
}

const POLL_INTERVAL = 30_000;

function formatPrice(val: number): string {
  return val.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function IndexTick({
  label,
  data,
  closed,
}: {
  label: string;
  data: IndexData;
  closed: boolean;
}) {
  const positive = data.change >= 0;
  const arrow = positive ? "\u25B2" : "\u25BC";
  const colorClass = positive
    ? "text-green-500"
    : "text-red-500";

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-gray-400 dark:text-gray-500 font-medium text-[11px]">
        {label}
      </span>
      <span className="text-gray-800 dark:text-gray-200 font-semibold font-mono text-xs">
        {formatPrice(data.price)}
      </span>
      {closed ? (
        <span className="text-gray-400 dark:text-gray-600 text-[10px]">
          Closed
        </span>
      ) : (
        <span
          className={`${colorClass} text-[11px] font-medium flex items-center gap-0.5`}
        >
          <span>{arrow}</span>
          <span>{Math.abs(data.change).toFixed(2)}</span>
          <span className="opacity-80">
            ({positive ? "+" : ""}
            {data.change_pct.toFixed(2)}%)
          </span>
        </span>
      )}
    </div>
  );
}

export function MarketTicker() {
  const [data, setData] = useState<MarketIndices | null>(
    null,
  );

  const fetchIndices = useCallback(async () => {
    try {
      const res = await apiFetch("/market/indices");
      if (res.ok) {
        const json: MarketIndices = await res.json();
        setData(json);
      }
    } catch {
      // Keep showing last known data.
    }
  }, []);

  useEffect(() => {
    fetchIndices();
    const id = setInterval(fetchIndices, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchIndices]);

  if (!data) return null;

  const closed = data.market_state === "CLOSED";

  return (
    <div className="hidden md:flex items-center gap-4 text-xs">
      {data.nifty?.price != null && (
        <IndexTick
          label="NIFTY"
          data={data.nifty}
          closed={closed}
        />
      )}
      <span className="text-gray-300 dark:text-gray-700">
        |
      </span>
      {data.sensex?.price != null && (
        <IndexTick
          label="SENSEX"
          data={data.sensex}
          closed={closed}
        />
      )}
    </div>
  );
}
