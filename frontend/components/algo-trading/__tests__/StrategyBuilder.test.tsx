import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

vi.mock("swr", () => ({
  default: () => ({ data: null, error: null, isLoading: false }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { StrategyBuilder } from "../builder/StrategyBuilder";

afterEach(() => cleanup());

describe("StrategyBuilder", () => {
  it("renders with the blank template by default", () => {
    render(<StrategyBuilder />);
    expect(screen.getByTestId("algo-strategy-builder")).toBeTruthy();
    expect(
      (screen.getByTestId("algo-builder-name") as HTMLInputElement).value,
    ).toBe("New strategy");
  });

  it("switches templates when a template button is clicked", () => {
    render(<StrategyBuilder />);
    fireEvent.click(screen.getByTestId("algo-builder-template-golden_cross"));
    expect(
      (screen.getByTestId("algo-builder-name") as HTMLInputElement).value,
    ).toBe("Golden cross v1");
  });

  it("renders a JSON preview pane", () => {
    render(<StrategyBuilder />);
    const json = screen.getByTestId("algo-builder-json");
    expect(json.textContent).toContain('"root"');
  });

  it("toggles paste mode and applies pasted JSON", () => {
    render(<StrategyBuilder />);
    fireEvent.click(screen.getByTestId("algo-builder-json-toggle"));
    const ta = screen.getByTestId("algo-builder-json-input");
    fireEvent.change(ta, {
      target: {
        value: JSON.stringify({
          id: "abcd",
          name: "Pasted",
          universe: {},
          schedule: {},
          rebalance: {},
          root: { type: "hold" },
          risk: {},
        }),
      },
    });
    fireEvent.click(screen.getByTestId("algo-builder-json-apply"));
    expect(
      (screen.getByTestId("algo-builder-name") as HTMLInputElement).value,
    ).toBe("Pasted");
  });
});
