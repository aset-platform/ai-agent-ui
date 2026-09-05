import { render, screen, fireEvent } from "@testing-library/react";
import { test, expect } from "vitest";
import ClosePositionModal from "../ClosePositionModal";

const holding = {
  ticker: "DLF.NS",
  quantity: 45,
  avg_price: 746.26,
  currency: "INR",
};

test("previews realized P&L from qty, sell price, fees", () => {
  render(
    <ClosePositionModal
      isOpen
      holding={holding}
      onClose={() => {}}
      onConfirm={() => {}}
    />,
  );
  fireEvent.change(screen.getByTestId("close-qty-input"), {
    target: { value: "20" },
  });
  fireEvent.change(screen.getByTestId("close-price-input"), {
    target: { value: "800" },
  });
  // (800 - 746.26) * 20 = 1074.80
  expect(
    screen.getByTestId("close-pnl-preview").textContent,
  ).toContain("1,074.80");
});
