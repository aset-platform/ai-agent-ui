import { describe, expect, it, vi, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

vi.mock("swr", () => ({
  default: () => ({ data: { strategies: [] }, error: null, isLoading: false }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { StrategiesTab } from "../StrategiesTab";

afterEach(() => cleanup());

describe("StrategiesTab", () => {
  it("renders empty state when no strategies", () => {
    render(<StrategiesTab />);
    expect(screen.getByTestId("algo-strategies-empty")).toBeTruthy();
  });

  it("opens builder mode when New strategy is clicked", () => {
    render(<StrategiesTab />);
    fireEvent.click(screen.getByTestId("algo-strategies-new"));
    expect(screen.getByTestId("algo-strategy-builder")).toBeTruthy();
  });

  it("returns to list when builder Cancel is clicked", () => {
    render(<StrategiesTab />);
    fireEvent.click(screen.getByTestId("algo-strategies-new"));
    fireEvent.click(screen.getByTestId("algo-builder-cancel"));
    expect(screen.getByTestId("algo-strategies-empty")).toBeTruthy();
  });
});
