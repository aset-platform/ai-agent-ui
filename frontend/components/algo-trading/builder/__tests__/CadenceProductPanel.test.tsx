import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";

import { CadenceProductPanel } from "../CadenceProductPanel";
import type { StrategyAst } from "@/hooks/useStrategies";

function _baseAst(overrides: Partial<StrategyAst> = {}): StrategyAst {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    name: "Test",
    universe: {},
    schedule: {
      type: "bar_close",
      interval: "1d",
      time: "15:25 IST",
    },
    rebalance: { type: "daily", max_positions: 10 },
    root: { type: "hold" },
    risk: {},
    ...overrides,
  } as unknown as StrategyAst;
}

afterEach(() => cleanup());

describe("CadenceProductPanel — ASETPLTFRM-395", () => {
  it("renders Daily + CNC defaults for a fresh AST", () => {
    const onChange = vi.fn();
    render(
      <CadenceProductPanel
        ast={_baseAst()}
        onChange={onChange}
      />,
    );
    const dailyRadio = screen.getByTestId(
      "algo-builder-cadence-1d",
    ) as HTMLInputElement;
    const cncRadio = screen.getByTestId(
      "algo-builder-product-CNC",
    ) as HTMLInputElement;
    expect(dailyRadio.checked).toBe(true);
    expect(cncRadio.checked).toBe(true);
  });

  it("hides square-off picker on CNC", () => {
    render(
      <CadenceProductPanel
        ast={_baseAst()}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("algo-builder-square-off-time"),
    ).toBeNull();
  });

  it("shows square-off picker when MIS is selected via prop", () => {
    render(
      <CadenceProductPanel
        ast={_baseAst({
          schedule: {
            type: "bar_close",
            interval: "5m",
            time: "15:14 IST",
          } as unknown,
          product: "MIS",
          square_off_time: "15:14 IST",
        })}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("algo-builder-square-off-time"),
    ).toBeTruthy();
  });

  it("disables Daily cadence radio when MIS is selected", () => {
    render(
      <CadenceProductPanel
        ast={_baseAst({
          schedule: {
            type: "bar_close",
            interval: "5m",
            time: "15:14 IST",
          } as unknown,
          product: "MIS",
        })}
        onChange={vi.fn()}
      />,
    );
    const dailyRadio = screen.getByTestId(
      "algo-builder-cadence-1d",
    ) as HTMLInputElement;
    expect(dailyRadio.disabled).toBe(true);
  });

  it(
    "auto-snaps cadence to 5m when switching to MIS from Daily",
    () => {
      const onChange = vi.fn();
      render(
        <CadenceProductPanel
          ast={_baseAst()}
          onChange={onChange}
        />,
      );
      fireEvent.click(
        screen.getByTestId("algo-builder-product-MIS"),
      );
      const next = onChange.mock.calls[0][0];
      expect(
        (next.schedule as { interval: string }).interval,
      ).toBe("5m");
      expect(next.product).toBe("MIS");
      // Defaults a square_off_time when first switching to MIS.
      expect(next.square_off_time).toBe("15:14 IST");
    },
  );

  it("changes cadence without touching the product field", () => {
    const onChange = vi.fn();
    render(
      <CadenceProductPanel
        ast={_baseAst({ product: "CNC" })}
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByTestId("algo-builder-cadence-15m"),
    );
    const next = onChange.mock.calls[0][0];
    expect(
      (next.schedule as { interval: string }).interval,
    ).toBe("15m");
    // Product preserved — cadence radio does not flip product.
    expect(next.product).toBe("CNC");
  });

  it("propagates a square-off time edit through onChange", () => {
    const onChange = vi.fn();
    render(
      <CadenceProductPanel
        ast={_baseAst({
          schedule: {
            type: "bar_close",
            interval: "5m",
            time: "15:14 IST",
          } as unknown,
          product: "MIS",
          square_off_time: "15:14 IST",
        })}
        onChange={onChange}
      />,
    );
    const input = screen.getByTestId(
      "algo-builder-square-off-time",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "15:10 IST" } });
    const next = onChange.mock.calls[0][0];
    expect(next.square_off_time).toBe("15:10 IST");
  });
});
