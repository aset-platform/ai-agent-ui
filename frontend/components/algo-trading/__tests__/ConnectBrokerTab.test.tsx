import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

const swrData = { current: null as unknown };

vi.mock("swr", () => ({
  default: () => ({
    data: swrData.current,
    error: null,
    isLoading: false,
  }),
  mutate: vi.fn(),
}));
vi.mock("@/lib/apiFetch", () => ({ apiFetch: vi.fn() }));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { ConnectBrokerTab } from "../ConnectBrokerTab";

afterEach(() => {
  cleanup();
  swrData.current = null;
});

describe("ConnectBrokerTab", () => {
  it("renders disconnected state with api-key form", () => {
    swrData.current = { status: "disconnected" };
    render(<ConnectBrokerTab />);
    expect(
      screen.getByTestId("algo-broker-status-disconnected"),
    ).toBeTruthy();
    expect(screen.getByTestId("algo-broker-api-key-input")).toBeTruthy();
  });

  it("renders key_set state with Connect Zerodha button", () => {
    swrData.current = { status: "key_set" };
    render(<ConnectBrokerTab />);
    expect(screen.getByTestId("algo-broker-connect")).toBeTruthy();
  });

  it("renders connected state with kite_user_id", () => {
    swrData.current = {
      status: "connected",
      kite_user_id: "AB1234",
    };
    render(<ConnectBrokerTab />);
    const card = screen.getByTestId("algo-broker-status-connected");
    expect(card.textContent).toContain("AB1234");
  });

  it("renders expired state with amber banner", () => {
    swrData.current = { status: "expired", kite_user_id: "AB1234" };
    render(<ConnectBrokerTab />);
    expect(
      screen.getByTestId("algo-broker-status-expired"),
    ).toBeTruthy();
    // Reconnect button visible
    expect(screen.getByTestId("algo-broker-connect")).toBeTruthy();
  });

  it("save-key button is disabled when input is empty", () => {
    swrData.current = { status: "disconnected" };
    render(<ConnectBrokerTab />);
    const btn = screen.getByTestId("algo-broker-save-key") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
