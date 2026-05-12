/**
 * Shared IST (Asia/Kolkata) date/time formatting helpers
 * (ASETPLTFRM-373).
 *
 * Before this module, ~20 components rolled their own
 * ``toLocaleString(..., { timeZone: "Asia/Kolkata" })`` invocations
 * with slightly different option bags. That made it easy to drift
 * (one place shows 24-hour, another 12-hour; one rounds to minute,
 * another to second) and risks SSR/CSR hydration mismatches when a
 * helper accidentally inherits the server's TZ.
 *
 * All helpers here:
 * - Pin ``timeZone: "Asia/Kolkata"`` so SSR and client agree.
 * - Pin ``hour12: false`` so 24-hour wall-clock is the canonical
 *   format. Indian financial markets quote times in 24-hour; mixing
 *   12-hour leads to confused reads of midday vs midnight.
 * - Accept ``Date | string | number`` so callers can pass an ISO
 *   string, ms epoch, or a Date directly.
 * - Return ``"—"`` for any unparseable input so the UI never breaks
 *   on a single malformed event.
 *
 * Per ``feedback_ist_dates_user_facing`` — user-facing dates render
 * in IST; backend internals stay UTC. This module is exclusively for
 * the user-facing boundary.
 */

export const IST_TZ = "Asia/Kolkata";

type DateInput = Date | string | number;

/** Coerce input to a Date; ``null`` on invalid. */
function toDate(input: DateInput): Date | null {
  if (input instanceof Date) {
    return Number.isFinite(input.getTime()) ? input : null;
  }
  const d = new Date(input);
  return Number.isFinite(d.getTime()) ? d : null;
}

const FALLBACK = "—";

/**
 * Today's IST date as ``YYYY-MM-DD``.
 *
 * Used by widgets that filter algo.events on the partition column
 * ``ts_date`` (e.g. RecentFillsTape, KitePostbackPanel) so prior
 * sessions don't bleed into the visible window. ``en-CA`` produces
 * ISO-style ``YYYY-MM-DD``; the explicit IST timezone keeps the
 * boundary at midnight IST regardless of viewer locale.
 */
export function todayIstIso(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: IST_TZ });
}

/**
 * IST date as ``YYYY-MM-DD`` for the given timestamp.
 *
 * Useful when bucketing arbitrary event timestamps by their IST
 * trading-day partition.
 */
export function formatIstDate(input: DateInput): string {
  const d = toDate(input);
  if (!d) return FALLBACK;
  return d.toLocaleDateString("en-CA", { timeZone: IST_TZ });
}

/**
 * IST wall-clock time. Default ``HH:MM:SS``; pass ``includeSeconds:
 * false`` for ``HH:MM``.
 */
export function formatIstTime(
  input: DateInput,
  opts: { includeSeconds?: boolean } = {},
): string {
  const d = toDate(input);
  if (!d) return FALLBACK;
  const includeSeconds = opts.includeSeconds ?? true;
  return d.toLocaleTimeString("en-IN", {
    timeZone: IST_TZ,
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" as const } : {}),
    hour12: false,
  });
}

/**
 * IST full datetime — used for tooltip "absolute time" labels and
 * any panel where the user expects "DD/MM/YYYY, HH:MM:SS IST".
 *
 * The default uses ``dateStyle: "short"`` + ``timeStyle: "medium"``
 * which renders e.g. ``12/05/26, 14:32:18`` in en-IN.
 */
export function formatIstDateTime(
  input: DateInput,
  opts: {
    dateStyle?: "short" | "medium" | "long" | "full";
    timeStyle?: "short" | "medium" | "long" | "full";
  } = {},
): string {
  const d = toDate(input);
  if (!d) return FALLBACK;
  return d.toLocaleString("en-IN", {
    timeZone: IST_TZ,
    dateStyle: opts.dateStyle ?? "short",
    timeStyle: opts.timeStyle ?? "medium",
    hour12: false,
  });
}

/**
 * Compact ``HH:MM`` for log-line tape views (Recent Fills, Events
 * feed). The seconds drop reduces visual noise in a list of 5-20
 * adjacent timestamps.
 */
export function formatIstTimeShort(input: DateInput): string {
  return formatIstTime(input, { includeSeconds: false });
}

/**
 * Low-level escape hatch — full ``Intl.DateTimeFormatOptions``
 * passthrough with ``timeZone`` pinned to IST. Use the named
 * helpers above where they fit; reach for this only when you need
 * a non-standard combo (e.g. "MMM DD, HH:MM" without a year).
 *
 * Pins ``hour12: false`` by default; pass ``hour12: true`` in
 * ``options`` to override.
 */
export function formatIst(
  input: DateInput,
  options: Intl.DateTimeFormatOptions = {},
): string {
  const d = toDate(input);
  if (!d) return FALLBACK;
  return d.toLocaleString("en-IN", {
    timeZone: IST_TZ,
    hour12: false,
    ...options,
  });
}

/**
 * Compatibility shim: nanoseconds-since-epoch → IST time.
 *
 * Algo events store ``ts_ns`` (ns precision, exchange-emitted). The
 * runtime expresses everything else as ms-epoch Dates; this avoids
 * every caller writing ``Math.floor(ts_ns / 1_000_000)``.
 */
export function formatIstTimeFromNs(
  tsNs: number | string | bigint,
  opts: { includeSeconds?: boolean } = {},
): string {
  const ns = typeof tsNs === "bigint" ? Number(tsNs) : Number(tsNs);
  if (!Number.isFinite(ns)) return FALLBACK;
  return formatIstTime(Math.floor(ns / 1_000_000), opts);
}
