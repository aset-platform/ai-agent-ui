/**
 * Page object for the Admin page (``/admin``).
 */

import { type Locator } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class AdminPage extends BasePage {
  /** Navigate to the admin page. */
  async gotoAdmin(): Promise<void> {
    await super.goto("/admin");
  }

  /** Click a tab by its identifier. */
  async clickTab(tabId: string): Promise<void> {
    await this.tid(FE.adminTab(tabId)).click();
  }

  // ── Users tab ──────────────────────────────────

  /** Type a query into the users search box. */
  async searchUsers(query: string): Promise<void> {
    const input = this.tid(FE.adminUsersSearch);
    await input.clear();
    await input.fill(query);
  }

  /** The users data table. */
  usersTable(): Locator {
    return this.tid(FE.adminUsersTable);
  }

  /** The "Add User" button. */
  addUserBtn(): Locator {
    return this.tid(FE.adminUsersAddBtn);
  }

  /** Edit button for a specific user row. */
  userEditBtn(userId: string): Locator {
    return this.tid(FE.adminUserEdit(userId));
  }

  /** Password-reset button for a user. */
  userResetBtn(userId: string): Locator {
    return this.tid(FE.adminUserReset(userId));
  }

  /** Enable/disable toggle for a user. */
  userToggleBtn(userId: string): Locator {
    return this.tid(FE.adminUserToggle(userId));
  }

  // ── Audit tab ──────────────────────────────────

  /** Type a query into the audit search box. */
  async searchAudit(query: string): Promise<void> {
    const input = this.tid(FE.adminAuditSearch);
    await input.clear();
    await input.fill(query);
  }

  /** The audit log data table. */
  auditTable(): Locator {
    return this.tid(FE.adminAuditTable);
  }

  // ── LLM Health tab ─────────────────────────────

  /** Tier health card for a given model. */
  tierCard(model: string): Locator {
    return this.tid(FE.adminTierCard(model));
  }

  /** Toggle button to enable/disable a tier. */
  tierToggleBtn(model: string): Locator {
    return this.tid(FE.adminTierToggle(model));
  }

  /** Budget usage card for a given model. */
  budgetCard(model: string): Locator {
    return this.tid(FE.adminBudgetCard(model));
  }

  /** The cascade events table. */
  cascadeTable(): Locator {
    return this.tid(FE.adminCascadeTable);
  }

  /** A summary stat card by name. */
  summaryCard(name: string): Locator {
    const key = name as
      | "requests"
      | "cascades"
      | "compressions";
    const map = {
      requests: FE.adminSummaryRequests,
      cascades: FE.adminSummaryCascades,
      compressions: FE.adminSummaryCompressions,
    } as const;
    return this.tid(map[key]);
  }
}
