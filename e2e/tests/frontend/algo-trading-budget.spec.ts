import { test, expect } from "@playwright/test";
import { BudgetPage } from "../../pages/frontend/budget.page";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Budget panel", () => {
  test("renders on Live tab", async ({ page }) => {
    const budget = new BudgetPage(page);
    await budget.open();
    await expect(
      page.getByTestId(FE.budgetPanel),
    ).toBeVisible();
  });
});
