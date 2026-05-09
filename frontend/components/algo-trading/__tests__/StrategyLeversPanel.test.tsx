import {
  cleanup, fireEvent, render, screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StrategyLeversPanel } from "../builder/StrategyLeversPanel";
import type { StrategyAst } from "@/hooks/useStrategies";

afterEach(() => {
  cleanup();
});

function _ast(): StrategyAst {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Test",
    universe: {
      type: "scope",
      scope: "watchlist",
      filter: { ticker_type: ["stock"], market: "india" },
    },
    schedule: {
      type: "bar_close", interval: "1d", time: "15:25 IST",
    },
    rebalance: { type: "daily", max_positions: 5 },
    root: { type: "hold" },
    risk: {
      per_trade: { stop_loss_pct: 5, max_qty: 100 },
      portfolio: {
        max_exposure_pct: 80,
        max_concentration_pct: 25,
      },
      daily: { max_loss_pct: 2, max_open_positions: 10 },
    },
  } as StrategyAst;
}


describe("StrategyLeversPanel", () => {
  it("renders panel + lever fields with current values", () => {
    const onChange = vi.fn();
    render(
      <StrategyLeversPanel ast={_ast()} onChange={onChange} />,
    );
    expect(
      screen.queryByTestId("strategy-levers-panel"),
    ).not.toBeNull();
    const scope = screen.getByTestId(
      "lever-universe-scope",
    ) as HTMLSelectElement;
    expect(scope.value).toBe("watchlist");
    const market = screen.getByTestId(
      "lever-universe-market",
    ) as HTMLSelectElement;
    expect(market.value).toBe("india");
    const expo = screen.getByTestId(
      "lever-risk-max-exposure-pct",
    ) as HTMLInputElement;
    expect(expo.value).toBe("80");
  });

  it("calls onChange with patched scope when dropdown changes", () => {
    const onChange = vi.fn();
    render(
      <StrategyLeversPanel ast={_ast()} onChange={onChange} />,
    );
    fireEvent.change(
      screen.getByTestId("lever-universe-scope"),
      { target: { value: "discovery" } },
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0];
    expect(next.universe.scope).toBe("discovery");
    // Other fields should be preserved.
    expect(next.universe.filter.market).toBe("india");
    expect(next.risk.per_trade.max_qty).toBe(100);
  });

  it("patches a numeric risk lever without touching other fields", () => {
    const onChange = vi.fn();
    render(
      <StrategyLeversPanel ast={_ast()} onChange={onChange} />,
    );
    fireEvent.change(
      screen.getByTestId("lever-risk-max-concentration-pct"),
      { target: { value: "15" } },
    );
    const next = onChange.mock.calls[0][0];
    expect(next.risk.portfolio.max_concentration_pct).toBe(15);
    expect(next.risk.portfolio.max_exposure_pct).toBe(80);
    expect(next.risk.per_trade.max_qty).toBe(100);
  });

  it("toggles ticker_type checkbox without leaving empty list", () => {
    const onChange = vi.fn();
    render(
      <StrategyLeversPanel ast={_ast()} onChange={onChange} />,
    );
    // Try to uncheck "stock" — but since it's the only one
    // selected, the panel must keep ≥1 type. Should be a no-op.
    const stockCb = screen.getByTestId(
      "lever-ticker-type-stock",
    ) as HTMLInputElement;
    expect(stockCb.checked).toBe(true);
    fireEvent.click(stockCb);
    expect(onChange).not.toHaveBeenCalled();
    // Adding "etf" succeeds.
    const etfCb = screen.getByTestId(
      "lever-ticker-type-etf",
    ) as HTMLInputElement;
    fireEvent.click(etfCb);
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0];
    expect(next.universe.filter.ticker_type).toContain("stock");
    expect(next.universe.filter.ticker_type).toContain("etf");
  });

  it("collapses + expands via the toggle button", () => {
    const onChange = vi.fn();
    render(
      <StrategyLeversPanel ast={_ast()} onChange={onChange} />,
    );
    expect(
      screen.queryByTestId("strategy-levers-body"),
    ).not.toBeNull();
    fireEvent.click(
      screen.getByTestId("strategy-levers-toggle"),
    );
    expect(
      screen.queryByTestId("strategy-levers-body"),
    ).toBeNull();
    fireEvent.click(
      screen.getByTestId("strategy-levers-toggle"),
    );
    expect(
      screen.queryByTestId("strategy-levers-body"),
    ).not.toBeNull();
  });
});
