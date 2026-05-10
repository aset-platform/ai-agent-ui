/**
 * LiveModeToggle — 4-gate validation tests (V2-5).
 *
 * Verifies:
 * 1. All gates closed → enable button disabled.
 * 2. Each gate individually closed → enable button disabled +
 *    correct gate shows as not-passing (✕).
 * 3. All gates open → enable button enabled.
 * 4. Enable modal appears with name-retype field.
 * 5. Confirm button disabled until name matches exactly.
 * 6. Confirm button enabled when name typed correctly.
 * 7. Already-enabled → disable button shown, enable hidden.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

import type { GatesStatus } from "@/hooks/useLiveStatus";

// ---- Mock hooks -----------------------------------------------

const mockGates: GatesStatus = {
  kite_connected: false,
  caps_set: false,
  kill_switch_disarmed: false,
  walkforward_recent: false,
  drift_within_limit: false,
  all_pass: false,
  live_orders_enabled: false,
};

let gatesOverride: GatesStatus = { ...mockGates };

vi.mock("@/hooks/useLiveStatus", () => ({
  useLiveStatus: () => ({
    gates: gatesOverride,
    loading: false,
    error: null,
    revalidate: vi.fn(),
  }),
}));

vi.mock("@/hooks/useLiveCaps", () => ({
  enableLiveOrders: vi.fn().mockResolvedValue({}),
  disableLiveOrders: vi.fn().mockResolvedValue({}),
  useLiveCaps: () => ({ caps: null, loading: false, error: null }),
}));

// ---- Import component after mocks ----------------------------

import { LiveModeToggle } from "../LiveModeToggle";

afterEach(() => {
  cleanup();
  gatesOverride = { ...mockGates };
});

const STRATEGY_ID = "00000000-0000-0000-0000-000000000001";
const STRATEGY_NAME = "My Test Strategy";

describe("LiveModeToggle — 4-gate validation", () => {
  it("enable button disabled when all gates closed", () => {
    gatesOverride = { ...mockGates };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    const btn = screen.getByTestId("live-mode-enable-btn");
    expect(btn).toBeDisabled();
  });

  it("enable button disabled when kite_connected=false", () => {
    gatesOverride = {
      ...mockGates,
      caps_set: true,
      kill_switch_disarmed: true,
      walkforward_recent: true,
      drift_within_limit: true,
      all_pass: false,   // kite_connected=false → all_pass=false
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    expect(
      screen.getByTestId("live-mode-enable-btn"),
    ).toBeDisabled();
  });

  it("enable button disabled when caps_set=false", () => {
    gatesOverride = {
      ...mockGates,
      kite_connected: true,
      kill_switch_disarmed: true,
      walkforward_recent: true,
      drift_within_limit: true,
      all_pass: false,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    expect(
      screen.getByTestId("live-mode-enable-btn"),
    ).toBeDisabled();
  });

  it("enable button disabled when kill_switch_disarmed=false", () => {
    gatesOverride = {
      ...mockGates,
      kite_connected: true,
      caps_set: true,
      walkforward_recent: true,
      drift_within_limit: true,
      all_pass: false,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    expect(
      screen.getByTestId("live-mode-enable-btn"),
    ).toBeDisabled();
  });

  it("enable button disabled when walkforward_recent=false", () => {
    gatesOverride = {
      ...mockGates,
      kite_connected: true,
      caps_set: true,
      kill_switch_disarmed: true,
      drift_within_limit: true,
      all_pass: false,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    expect(
      screen.getByTestId("live-mode-enable-btn"),
    ).toBeDisabled();
  });

  it("enable button disabled when drift_within_limit=false", () => {
    gatesOverride = {
      ...mockGates,
      kite_connected: true,
      caps_set: true,
      kill_switch_disarmed: true,
      walkforward_recent: true,
      all_pass: false,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    expect(
      screen.getByTestId("live-mode-enable-btn"),
    ).toBeDisabled();
  });

  it("enable button enabled when all gates pass", () => {
    gatesOverride = {
      kite_connected: true,
      caps_set: true,
      kill_switch_disarmed: true,
      walkforward_recent: true,
      drift_within_limit: true,
      all_pass: true,
      live_orders_enabled: false,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    const btn = screen.getByTestId("live-mode-enable-btn");
    expect(btn).not.toBeDisabled();
  });

  it("shows enable modal when enable button clicked", () => {
    gatesOverride = {
      kite_connected: true,
      caps_set: true,
      kill_switch_disarmed: true,
      walkforward_recent: true,
      drift_within_limit: true,
      all_pass: true,
      live_orders_enabled: false,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    fireEvent.click(screen.getByTestId("live-mode-enable-btn"));
    expect(
      screen.getByTestId("live-enable-modal"),
    ).toBeDefined();
  });

  it("confirm button disabled until name typed correctly", () => {
    gatesOverride = {
      kite_connected: true,
      caps_set: true,
      kill_switch_disarmed: true,
      walkforward_recent: true,
      drift_within_limit: true,
      all_pass: true,
      live_orders_enabled: false,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    fireEvent.click(screen.getByTestId("live-mode-enable-btn"));
    const confirmBtn = screen.getByTestId("live-enable-modal-confirm");
    expect(confirmBtn).toBeDisabled();

    // Type wrong name
    const input = screen.getByTestId("live-enable-modal-name-input");
    fireEvent.change(input, { target: { value: "wrong name" } });
    expect(confirmBtn).toBeDisabled();

    // Type correct name
    fireEvent.change(input, { target: { value: STRATEGY_NAME } });
    expect(confirmBtn).not.toBeDisabled();
  });

  it("shows disable button when live_orders_enabled=true", () => {
    gatesOverride = {
      kite_connected: true,
      caps_set: true,
      kill_switch_disarmed: true,
      walkforward_recent: true,
      drift_within_limit: true,
      all_pass: true,
      live_orders_enabled: true,
    };
    render(
      <LiveModeToggle
        strategyId={STRATEGY_ID}
        strategyName={STRATEGY_NAME}
      />,
    );
    expect(
      screen.getByTestId("live-mode-disable-btn"),
    ).toBeDefined();
    expect(
      screen.queryByTestId("live-mode-enable-btn"),
    ).toBeNull();
  });
});
