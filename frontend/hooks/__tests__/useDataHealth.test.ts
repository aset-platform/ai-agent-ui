import { describe, it, expect } from "vitest";
import { DATA_HEALTH_SWR_OPTS } from "../useAdminData";

describe("useDataHealth SWR opts", () => {
  it("dedupes for 60s to match Redis TTL", () => {
    expect(DATA_HEALTH_SWR_OPTS.dedupingInterval).toBe(60_000);
    expect(DATA_HEALTH_SWR_OPTS.revalidateOnFocus).toBe(false);
  });
});
