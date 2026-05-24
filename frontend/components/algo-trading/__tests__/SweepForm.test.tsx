import {
  cleanup, fireEvent, render, screen, waitFor,
} from "@testing-library/react";
import {
  afterEach, describe, it, expect, vi, beforeEach,
} from "vitest";
import { SweepForm } from "../SweepForm";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("@/hooks/useSweepableFields", () => ({
  useSweepableFields: () => ({
    fields: [
      {
        key: "cooldown_days",
        label: "Cooldown (days)",
        field_type: "int",
        min_value: "0",
        max_value: "60",
      },
      {
        key: "stop_loss_pct",
        label: "Stop loss %",
        field_type: "decimal",
        min_value: "0.5",
        max_value: "20.0",
      },
    ],
    isLoading: false,
    error: null,
  }),
}));

vi.mock("@/hooks/useStrategies", () => ({
  useStrategies: () => ({
    strategies: [
      { id: "strat-1", name: "RSI(2) v3" },
    ],
    isLoading: false,
  }),
}));

describe("SweepForm", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => cleanup());

  it("renders field dropdown with whitelist", async () => {
    render(<SweepForm onStarted={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByText("Cooldown (days)"),
      ).toBeDefined();
    });
  });

  it("disables submit when fewer than 2 values", async () => {
    render(<SweepForm onStarted={vi.fn()} />);
    const valuesInput = (await screen.findByTestId(
      "sweep-values-input",
    )) as HTMLInputElement;
    fireEvent.change(valuesInput, { target: { value: "7" } });
    const btn = screen.getByTestId("sweep-submit");
    expect(btn.hasAttribute("disabled")).toBe(true);
  });

  it("enables submit when 2+ values entered", async () => {
    render(<SweepForm onStarted={vi.fn()} />);
    const stratSelect = (await screen.findByTestId(
      "sweep-base-strategy-select",
    )) as HTMLSelectElement;
    fireEvent.change(stratSelect, {
      target: { value: "strat-1" },
    });
    const fieldSelect = (await screen.findByTestId(
      "sweep-field-select",
    )) as HTMLSelectElement;
    fireEvent.change(fieldSelect, {
      target: { value: "cooldown_days" },
    });
    const valuesInput = (await screen.findByTestId(
      "sweep-values-input",
    )) as HTMLInputElement;
    fireEvent.change(valuesInput, {
      target: { value: "3, 7, 14" },
    });
    const btn = screen.getByTestId("sweep-submit");
    expect(btn.hasAttribute("disabled")).toBe(false);
  });
});
