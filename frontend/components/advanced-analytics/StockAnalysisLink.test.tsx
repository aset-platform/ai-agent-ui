/// <reference types="@testing-library/jest-dom/vitest" />
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  StockAnalysisLink,
  stockAnalysisUrl,
} from "./StockAnalysisLink";

describe("stockAnalysisUrl", () => {
  it("returns a URL containing the ticker", () => {
    const url = stockAnalysisUrl("INFY.NS");
    expect(url).toContain("INFY.NS");
  });
});

describe("<StockAnalysisLink />", () => {
  it("renders an anchor with the correct href, target, rel and aria-label", () => {
    render(<StockAnalysisLink ticker="INFY.NS" />);
    const a = screen.getByRole("link");
    expect(a).toHaveAttribute("href", stockAnalysisUrl("INFY.NS"));
    expect(a).toHaveAttribute("target", "_blank");
    expect(a).toHaveAttribute("rel", "noopener noreferrer");
    expect(a).toHaveAttribute(
      "aria-label",
      "Open stock analysis for INFY.NS",
    );
  });

  it("uses the default testid when none is supplied", () => {
    render(<StockAnalysisLink ticker="ITC.NS" />);
    expect(
      screen.getByTestId("stock-analysis-link-ITC.NS"),
    ).toBeInTheDocument();
  });

  it("uses the supplied testid when provided", () => {
    render(
      <StockAnalysisLink ticker="ITC.NS" testId="aa-chart-link-ITC.NS" />,
    );
    expect(
      screen.getByTestId("aa-chart-link-ITC.NS"),
    ).toBeInTheDocument();
  });
});
