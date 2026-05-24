import { BasePage } from "../base.page";
import { FE } from "../../utils/selectors";

export class BudgetPage extends BasePage {
  async open() {
    await this.page.goto(
      "/algo-trading/strategies?tab=live",
    );
    await this.tid(FE.budgetPanel).waitFor();
  }

  async clickAllocate() {
    await this.tid(FE.budgetTileEditButton).click();
    await this.tid(FE.budgetAllocationModal).waitFor();
  }

  async setAllocation(amount: string) {
    await this.tid(FE.budgetAllocationInput).fill(amount);
    await this.tid(FE.budgetAllocationSaveButton).click();
  }
}
