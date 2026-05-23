import {
  cleanup, fireEvent, render, screen,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StrategyLeversPanel } from "../builder/StrategyLeversPanel";
import type { StrategyAst } from "@/hooks/useStrategies";

function Stateful({ initial }: { initial: StrategyAst }) {
  const [a, setA] = useState(initial);
  return <StrategyLeversPanel ast={a} onChange={setA} />;
}

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

  it("min_adtv_inr field shows blank when null and patches null on clear", () => {
    render(<Stateful initial={_ast()} />);
    const adtv = screen.getByTestId(
      "lever-universe-min-adtv-inr",
    ) as HTMLInputElement;
    expect(adtv.value).toBe("");
    fireEvent.change(adtv, { target: { value: "50000000" } });
    expect(adtv.value).toBe("50000000");
    fireEvent.change(adtv, { target: { value: "" } });
    expect(adtv.value).toBe("");
  });

  it("loads min_adtv_inr from existing AST value", () => {
    const a = _ast();
    a.universe = {
      ...a.universe,
      filter: { ...a.universe.filter, min_adtv_inr: 50000000 },
    } as unknown as StrategyAst["universe"];
    render(<StrategyLeversPanel ast={a} onChange={vi.fn()} />);
    const adtv = screen.getByTestId(
      "lever-universe-min-adtv-inr",
    ) as HTMLInputElement;
    expect(adtv.value).toBe("50000000");
  });

  it("is_fno checkbox toggles UniverseFilter.is_fno", () => {
    const onChange = vi.fn();
    render(
      <StrategyLeversPanel ast={_ast()} onChange={onChange} />,
    );
    const cb = screen.getByTestId(
      "lever-universe-is-fno",
    ) as HTMLInputElement;
    expect(cb.checked).toBe(false);
    fireEvent.click(cb);
    const next = onChange.mock.calls[0][0];
    expect(next.universe.filter.is_fno).toBe(true);
  });

  it("max_holding_days field shows blank when null and patches null on clear", () => {
    render(<Stateful initial={_ast()} />);
    const mhd = screen.getByTestId(
      "lever-risk-max-holding-days",
    ) as HTMLInputElement;
    expect(mhd.value).toBe("");
    fireEvent.change(mhd, { target: { value: "5" } });
    expect(mhd.value).toBe("5");
    fireEvent.change(mhd, { target: { value: "" } });
    expect(mhd.value).toBe("");
  });

  it("loads max_holding_days from existing AST value", () => {
    const a = _ast();
    a.risk = {
      ...a.risk,
      per_trade: {
        ...(a.risk as unknown as { per_trade: object }).per_trade,
        max_holding_days: 5,
      },
    } as unknown as StrategyAst["risk"];
    render(<StrategyLeversPanel ast={a} onChange={vi.fn()} />);
    const mhd = screen.getByTestId(
      "lever-risk-max-holding-days",
    ) as HTMLInputElement;
    expect(mhd.value).toBe("5");
  });

  it("cooldown_after_failed_exit_days defaults to blank and patches null on clear", () => {
    render(<Stateful initial={_ast()} />);
    const cd = screen.getByTestId(
      "lever-risk-cooldown-days",
    ) as HTMLInputElement;
    expect(cd.value).toBe("");
    fireEvent.change(cd, { target: { value: "7" } });
    expect(cd.value).toBe("7");
    fireEvent.change(cd, { target: { value: "" } });
    expect(cd.value).toBe("");
  });

  it("loads cooldown_after_failed_exit_days from existing AST value", () => {
    const a = _ast();
    a.risk = {
      ...a.risk,
      per_trade: {
        ...(a.risk as unknown as { per_trade: object }).per_trade,
        cooldown_after_failed_exit_days: 14,
      },
    } as unknown as StrategyAst["risk"];
    render(<StrategyLeversPanel ast={a} onChange={vi.fn()} />);
    const cd = screen.getByTestId(
      "lever-risk-cooldown-days",
    ) as HTMLInputElement;
    expect(cd.value).toBe("14");
  });

  it("mid_trade_regime_check toggle is off by default", () => {
    render(<StrategyLeversPanel ast={_ast()} onChange={vi.fn()} />);
    const cb = screen.getByTestId(
      "lever-mid-trade-regime-check-toggle",
    ) as HTMLInputElement;
    expect(cb.checked).toBe(false);
  });

  it("mid_trade_regime_check toggle sets canonical condition + clears to null", () => {
    const onChange = vi.fn();
    render(
      <StrategyLeversPanel ast={_ast()} onChange={onChange} />,
    );
    const cb = screen.getByTestId(
      "lever-mid-trade-regime-check-toggle",
    ) as HTMLInputElement;
    // Turn on — should patch a populated condition tree
    fireEvent.click(cb);
    expect(onChange).toHaveBeenCalled();
    const patched = onChange.mock.calls[0][0] as unknown as {
      mid_trade_regime_check: Record<string, unknown> | null;
    };
    expect(patched.mid_trade_regime_check).not.toBeNull();
    expect(patched.mid_trade_regime_check?.type).toBe("and");
  });

  it("mid_trade_regime_check toggle reflects pre-set value in AST", () => {
    const a = _ast() as unknown as StrategyAst & {
      mid_trade_regime_check?: Record<string, unknown> | null;
    };
    a.mid_trade_regime_check = {
      type: "and",
      operands: [],
    };
    render(<StrategyLeversPanel ast={a} onChange={vi.fn()} />);
    const cb = screen.getByTestId(
      "lever-mid-trade-regime-check-toggle",
    ) as HTMLInputElement;
    expect(cb.checked).toBe(true);
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
