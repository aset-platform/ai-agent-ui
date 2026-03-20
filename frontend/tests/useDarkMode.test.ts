/**
 * Smoke test for the useDomDark hook export.
 */

import { describe, it, expect } from "vitest";
import { useDomDark } from "@/components/charts/useDarkMode";

describe("useDomDark", () => {
  it("exports useDomDark as a function", () => {
    expect(typeof useDomDark).toBe("function");
  });
});
