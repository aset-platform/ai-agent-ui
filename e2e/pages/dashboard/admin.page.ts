/**
 * Page object for the Dash admin page (``/admin/users``).
 */

import { type Locator, expect } from "@playwright/test";

import { DASH } from "../../utils/selectors";
import { waitForDashLoading } from "../../utils/wait.helper";
import { BasePage } from "../base.page";

export class DashAdminPage extends BasePage {
  get userTable() {
    return this.tid(DASH.adminUserTable);
  }
  get createBtn() {
    return this.tid(DASH.adminCreateBtn);
  }
  get auditLogBtn() {
    return this.tid(DASH.adminAuditLogBtn);
  }

  /** Navigate to admin page with superuser JWT. */
  async gotoWithToken(token: string): Promise<void> {
    await this.page.goto(`/admin/users?token=${token}`);
    await waitForDashLoading(this.page);
    // Retry once if Dash restarted mid-load
    const err = this.page.locator("text=Callback error");
    if ((await err.count()) > 0) {
      await this.page.waitForTimeout(3_000);
      await this.page.reload();
      await waitForDashLoading(this.page);
    }
  }

  /** Get all rows in the user table. */
  get userRows(): Locator {
    return this.userTable.locator("tbody tr");
  }

  /** Assert the user table has at least N rows. */
  async expectMinUsers(count: number): Promise<void> {
    await expect(this.userRows).toHaveCount(count, {
      timeout: 10_000,
    });
  }
}
