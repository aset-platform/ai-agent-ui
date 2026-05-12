import { afterEach, describe, expect, it, vi } from "vitest";

import {
  IST_TZ,
  formatIstDate,
  formatIstDateTime,
  formatIstTime,
  formatIstTimeFromNs,
  formatIstTimeShort,
  todayIstIso,
} from "../datetime";

// All assertions pin a known instant so the test isn't sensitive to
// the host clock. 2026-05-12T09:00:00Z = 2026-05-12T14:30:00 IST —
// matches the daily-bar warmup eval gate, chosen as a memorable
// reference point.
const REF_UTC_MS = Date.UTC(2026, 4, 12, 9, 0, 0); // May=4 (0-indexed)
const REF_ISO = "2026-05-12T09:00:00.000Z";

describe("datetime — IST helpers", () => {
  afterEach(() => vi.useRealTimers());

  it("exports the IANA timezone string", () => {
    expect(IST_TZ).toBe("Asia/Kolkata");
  });

  describe("todayIstIso", () => {
    it("returns today's IST date in YYYY-MM-DD form", () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date(REF_UTC_MS));
      // 09:00 UTC = 14:30 IST → still 2026-05-12 in IST.
      expect(todayIstIso()).toBe("2026-05-12");
    });

    it("rolls over at IST midnight, not UTC midnight", () => {
      vi.useFakeTimers();
      // 19:30 UTC = 01:00 IST next day.
      vi.setSystemTime(new Date(Date.UTC(2026, 4, 12, 19, 30)));
      expect(todayIstIso()).toBe("2026-05-13");
    });
  });

  describe("formatIstDate", () => {
    it("accepts an ISO string", () => {
      expect(formatIstDate(REF_ISO)).toBe("2026-05-12");
    });

    it("accepts a Date", () => {
      expect(formatIstDate(new Date(REF_UTC_MS))).toBe("2026-05-12");
    });

    it("accepts ms-since-epoch number", () => {
      expect(formatIstDate(REF_UTC_MS)).toBe("2026-05-12");
    });

    it("returns the dash fallback for invalid input", () => {
      expect(formatIstDate("not-a-date")).toBe("—");
      expect(formatIstDate(Number.NaN)).toBe("—");
    });
  });

  describe("formatIstTime", () => {
    it("renders 24-hour HH:MM:SS in IST by default", () => {
      // 09:00 UTC → 14:30:00 IST
      expect(formatIstTime(REF_ISO)).toBe("14:30:00");
    });

    it("drops seconds when includeSeconds=false", () => {
      expect(
        formatIstTime(REF_ISO, { includeSeconds: false }),
      ).toBe("14:30");
    });

    it("falls back on invalid input", () => {
      expect(formatIstTime("garbage")).toBe("—");
    });
  });

  describe("formatIstTimeShort", () => {
    it("is HH:MM (no seconds)", () => {
      expect(formatIstTimeShort(REF_ISO)).toBe("14:30");
    });
  });

  describe("formatIstDateTime", () => {
    it("includes both date and time, IST", () => {
      const out = formatIstDateTime(REF_ISO);
      // en-IN short = "DD/MM/YY" or "DD/MM/YYYY" depending on
      // ICU version; just assert the date components are present
      // in some order along with the time.
      expect(out).toMatch(/12\/05/);
      expect(out).toMatch(/14:30/);
    });
  });

  describe("formatIstTimeFromNs", () => {
    it("converts ns to ms then formats", () => {
      const ns = REF_UTC_MS * 1_000_000;
      expect(formatIstTimeFromNs(ns)).toBe("14:30:00");
    });

    it("handles bigint inputs", () => {
      // BigInt(...) avoids the ES2020-only `n` suffix; the tsconfig
      // target is ES2018 across this monorepo.
      const ns = BigInt(REF_UTC_MS) * BigInt(1_000_000);
      expect(formatIstTimeFromNs(ns)).toBe("14:30:00");
    });

    it("falls back on non-finite", () => {
      expect(formatIstTimeFromNs(Number.NaN)).toBe("—");
    });
  });
});
