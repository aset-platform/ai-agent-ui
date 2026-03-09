/**
 * Page object for the Edit Profile and Change Password modals.
 */

import { expect } from "@playwright/test";

import { FE } from "../../utils/selectors";
import { BasePage } from "../base.page";

export class ProfileModalPage extends BasePage {
  get editProfileModal() {
    return this.tid(FE.editProfileModal);
  }
  get changePasswordModal() {
    return this.tid(FE.changePasswordModal);
  }
  get profileAvatar() {
    return this.tid(FE.profileAvatar);
  }

  /** Open the profile dropdown menu. */
  async openProfileMenu(): Promise<void> {
    await this.profileAvatar.click();
  }

  /** Click the "Edit Profile" option from the dropdown. */
  async openEditProfile(): Promise<void> {
    await this.openProfileMenu();
    await this.page
      .getByRole("button", { name: /edit profile/i })
      .click();
    await expect(this.editProfileModal).toBeVisible();
  }

  /** Click the "Change Password" option. */
  async openChangePassword(): Promise<void> {
    await this.openProfileMenu();
    await this.page
      .getByRole("button", { name: /change password/i })
      .click();
    await expect(this.changePasswordModal).toBeVisible();
  }

  /** Click "Sign Out" from the profile dropdown. */
  async signOut(): Promise<void> {
    await this.openProfileMenu();
    await this.page
      .getByRole("button", { name: /sign out/i })
      .click();
  }
}
