import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { DryRunArmBanner } from "@/components/algo-trading/dryrun/DryRunArmBanner";

afterEach(() => cleanup());

describe("DryRunArmBanner", () => {
  it("renders disarmed state with Arm button", () => {
    render(<DryRunArmBanner armed={false} onToggle={() => {}} />);
    expect(screen.getByText(/dry-run is OFF/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /arm dry-run/i }),
    ).toBeTruthy();
  });

  it("renders armed state with Disarm button", () => {
    render(<DryRunArmBanner armed={true} onToggle={() => {}} />);
    expect(screen.getByText(/dry-run is ON/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /disarm dry-run/i }),
    ).toBeTruthy();
  });

  it("fires onToggle with the opposite state", () => {
    const onToggle = vi.fn();
    render(<DryRunArmBanner armed={false} onToggle={onToggle} />);
    fireEvent.click(
      screen.getByRole("button", { name: /arm dry-run/i }),
    );
    expect(onToggle).toHaveBeenCalledWith(true);
  });
});
