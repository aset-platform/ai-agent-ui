/**
 * Page object for the Parameter Sweep sub-tab under
 * Algo Trading → Strategies → Backtest.
 */

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class SweepPage extends BasePage {
  async open() {
    await this.page.goto(
      "/algo-trading/strategies?tab=backtest",
    );
    await this.page.click(`text=Parameter sweep`);
    await this.tid(FE.sweepForm).waitFor();
  }

  async selectStrategy(name: string) {
    await this.tid(FE.sweepBaseStrategySelect)
      .selectOption({ label: name });
  }

  async setField(label: string) {
    await this.tid(FE.sweepFieldSelect)
      .selectOption({ label });
  }

  async setValues(csv: string) {
    await this.tid(FE.sweepValuesInput).fill(csv);
  }

  async submit() {
    await this.tid(FE.sweepSubmit).click();
  }

  async waitForResults() {
    await this.tid(FE.sweepResultsTable).waitFor({
      state: "attached", timeout: 180_000,
    });
  }
}
