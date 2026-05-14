"use client";
/**
 * CadenceProductPanel — Builder controls for schedule.interval +
 * top-level product / square_off_time (ASETPLTFRM-395).
 *
 * Pure radio inputs + a conditional time picker. State lives in the
 * parent (StrategyBuilder); this component is a thin lens over the
 * AST's schedule, product, and square_off_time fields.
 *
 * Enforced invariants (mirrors the backend AST validator):
 *   • Product = MIS forces a non-1d cadence — the Daily radio is
 *     visually disabled when MIS is selected, with a tooltip
 *     explaining why. Backend validator catches the same case at
 *     parse time.
 *   • Square-off time picker only appears when Product = MIS.
 */

import type { StrategyAst } from "@/hooks/useStrategies";

type Interval = "1d" | "15m" | "5m" | "1m";
type Product = "CNC" | "MIS";

interface ScheduleBarClose {
  type: "bar_close";
  interval: Interval;
  time?: string;
}

interface Props {
  ast: StrategyAst;
  onChange: (next: StrategyAst) => void;
}

const CADENCE_OPTIONS: { value: Interval; label: string }[] = [
  { value: "1d", label: "Daily (1d)" },
  { value: "15m", label: "15-min" },
  { value: "5m", label: "5-min" },
  { value: "1m", label: "1-min" },
];

const PRODUCT_OPTIONS: { value: Product; label: string }[] = [
  { value: "CNC", label: "CNC (Delivery)" },
  { value: "MIS", label: "MIS (Intraday)" },
];

export function CadenceProductPanel({ ast, onChange }: Props) {
  const schedule = (ast.schedule as ScheduleBarClose) ?? {
    type: "bar_close",
    interval: "1d",
    time: "15:25 IST",
  };
  const interval = schedule.interval;
  const product = ast.product ?? "CNC";
  const squareOffTime = ast.square_off_time ?? "15:14 IST";
  // Default the cutoff to (square-off − 60min) when the AST
  // doesn't pin one. Pure display fallback — backend validator
  // stamps the same default at parse time when product=MIS.
  const entryCutoffTime =
    ast.entry_cutoff_time ?? defaultCutoff(squareOffTime);

  function setInterval(next: Interval) {
    onChange({
      ...ast,
      schedule: { ...schedule, interval: next } as unknown,
    });
  }

  function setProduct(next: Product) {
    // If switching to MIS while cadence is daily, auto-snap to 5m
    // so the AST stays valid (the backend rejects MIS + 1d).
    let nextSchedule = schedule;
    if (next === "MIS" && interval === "1d") {
      nextSchedule = { ...schedule, interval: "5m" };
    }
    onChange({
      ...ast,
      schedule: nextSchedule as unknown,
      product: next,
      // Default square-off time when first switching to MIS.
      ...(next === "MIS" && !ast.square_off_time
        ? { square_off_time: "15:14 IST" }
        : {}),
    });
  }

  function setSquareOffTime(next: string) {
    onChange({ ...ast, square_off_time: next });
  }

  function setEntryCutoffTime(next: string) {
    onChange({ ...ast, entry_cutoff_time: next });
  }

  const dailyDisabled = product === "MIS";

  return (
    <fieldset
      data-testid="algo-builder-cadence-product"
      className="rounded border border-gray-200 dark:border-gray-700
        bg-white dark:bg-gray-900 p-3"
    >
      <legend
        className="px-1 text-[11px] font-semibold uppercase
          tracking-wide text-gray-500 dark:text-gray-400"
      >
        Cadence &amp; Product
      </legend>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Cadence radios */}
        <div>
          <span
            className="block text-[11px] font-medium
              text-slate-600 dark:text-slate-300 mb-1"
          >
            Cadence
          </span>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {CADENCE_OPTIONS.map((opt) => {
              const isDisabled =
                opt.value === "1d" && dailyDisabled;
              return (
                <label
                  key={opt.value}
                  className={
                    "inline-flex items-center gap-1 text-[12px] " +
                    (isDisabled
                      ? "opacity-40 cursor-not-allowed"
                      : "cursor-pointer")
                  }
                  title={
                    isDisabled
                      ? "MIS requires intraday cadence — pick 15m, "
                        + "5m, or 1m, or switch product to CNC."
                      : undefined
                  }
                >
                  <input
                    type="radio"
                    name="algo-builder-cadence"
                    value={opt.value}
                    checked={interval === opt.value}
                    disabled={isDisabled}
                    onChange={() => setInterval(opt.value)}
                    data-testid={
                      `algo-builder-cadence-${opt.value}`
                    }
                  />
                  {opt.label}
                </label>
              );
            })}
          </div>
        </div>

        {/* Product radios */}
        <div>
          <span
            className="block text-[11px] font-medium
              text-slate-600 dark:text-slate-300 mb-1"
          >
            Product
          </span>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {PRODUCT_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className="inline-flex items-center gap-1 text-[12px]
                  cursor-pointer"
              >
                <input
                  type="radio"
                  name="algo-builder-product"
                  value={opt.value}
                  checked={product === opt.value}
                  onChange={() => setProduct(opt.value)}
                  data-testid={`algo-builder-product-${opt.value}`}
                />
                {opt.label}
              </label>
            ))}
          </div>
          {product === "MIS" && (
            <p
              className="mt-1 text-[10px] text-amber-700
                dark:text-amber-300"
            >
              MIS leverages ~5×. Your ₹ cap below is interpreted as
              <strong> notional spent</strong>, not margin — ₹3 000
              opens up to ₹3 000 of position with ~₹600 of margin.
            </p>
          )}
        </div>
      </div>

      {/* Square-off + entry-cutoff time pickers (MIS only) */}
      {product === "MIS" && (
        <div className="mt-3 space-y-2">
          <label
            className="flex items-center gap-2 text-[12px]"
          >
            <span
              className="text-[11px] font-medium text-slate-600
                dark:text-slate-300 w-36"
            >
              Square-off time (IST)
            </span>
            <input
              type="text"
              value={squareOffTime}
              onChange={(e) => setSquareOffTime(e.target.value)}
              placeholder="15:14 IST"
              data-testid="algo-builder-square-off-time"
              className="w-32 rounded border border-gray-300
                dark:border-gray-700 bg-white dark:bg-gray-900
                px-2 py-1 text-[12px]"
            />
            <span className="text-[10px] text-slate-500">
              Default 15:14 IST — one minute before Zerodha&apos;s
              broker-side auto-square at 15:15.
            </span>
          </label>
          <label
            className="flex items-center gap-2 text-[12px]"
          >
            <span
              className="text-[11px] font-medium text-slate-600
                dark:text-slate-300 w-36"
            >
              No-new-entries after (IST)
            </span>
            <input
              type="text"
              value={entryCutoffTime}
              onChange={(e) => setEntryCutoffTime(e.target.value)}
              placeholder="14:14 IST"
              data-testid="algo-builder-entry-cutoff-time"
              className="w-32 rounded border border-gray-300
                dark:border-gray-700 bg-white dark:bg-gray-900
                px-2 py-1 text-[12px]"
            />
            <span className="text-[10px] text-slate-500">
              Skips BUY signals at-or-after this time so open
              positions have headroom before square-off. Defaults
              to square-off − 60 min. SELL / exit signals are
              always honoured.
            </span>
          </label>
        </div>
      )}
    </fieldset>
  );
}

function defaultCutoff(squareOff: string): string {
  // "HH:MM[:SS] IST" → subtract 60 min, return "HH:MM IST".
  const m = squareOff.match(/^\s*(\d{1,2}):(\d{2})/);
  if (!m) return "14:14 IST";
  let h = parseInt(m[1], 10);
  let mn = parseInt(m[2], 10) - 60;
  while (mn < 0) {
    mn += 60;
    h -= 1;
  }
  if (h < 0) h = 0;
  return `${h.toString().padStart(2, "0")}:${mn
    .toString()
    .padStart(2, "0")} IST`;
}
