"use client";
/**
 * Pro user's "My Account" admin tab — the only tab a pro
 * user gets that exposes profile-mutation affordances.
 *
 * Renders: profile card with read-only fields + an
 * "Edit profile" button (reuses the canonical
 * `EditProfileModal`) + a "Change password" button
 * (reuses `ChangePasswordModal`).  Intentionally omits
 * role, is_active, subscription, and page_permissions —
 * those are superuser-only surface.
 */

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import {
  useEditProfile,
  type UserProfile,
} from "@/hooks/useEditProfile";
import { useChangePassword } from "@/hooks/useChangePassword";
import { EditProfileModal } from "@/components/EditProfileModal";
import { ChangePasswordModal } from "@/components/ChangePasswordModal";

export function MyAccountTab() {
  const [profile, setProfile] =
    useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const editProfile = useEditProfile();
  const changePassword = useChangePassword();

  useEffect(() => {
    let alive = true;
    setLoading(true);
    apiFetch(`${API_URL}/auth/me`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<UserProfile>;
      })
      .then((p) => {
        if (alive) setProfile(p);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(
          e instanceof Error
            ? e.message
            : "Failed to load profile",
        );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const handleEditSave = async (
    fullName: string,
    avatarFile: File | null,
  ) => {
    const updated = await editProfile.save(
      fullName, avatarFile,
    );
    if (updated) setProfile(updated);
  };

  const handlePasswordSave = async (
    newPw: string,
    confirmPw: string,
  ) => {
    await changePassword.save(
      profile?.email ?? "", newPw, confirmPw,
    );
  };

  if (loading) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
        Loading…
      </p>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 p-4 text-sm text-red-600 dark:text-red-400">
        {error}
      </div>
    );
  }
  if (!profile) return null;

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/60 p-5">
        <h3 className="text-[15px] font-bold text-gray-900 dark:text-gray-100">
          My Account
        </h3>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
          Update your display name, avatar, and password.
        </p>

        <dl className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <Field label="Full name" value={profile.full_name} />
          <Field label="Email" value={profile.email} mono />
          <Field label="Role" value={profile.role} />
          <Field
            label="Status"
            value={profile.is_active ? "Active" : "Inactive"}
          />
        </dl>

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => editProfile.open()}
            className="rounded-lg bg-indigo-600 hover:bg-indigo-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Edit profile
          </button>
          <button
            type="button"
            onClick={() => changePassword.open()}
            className="rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            Change password
          </button>
        </div>
      </div>

      <EditProfileModal
        isOpen={editProfile.isOpen}
        initialTab="profile"
        profile={profile}
        saving={editProfile.saving}
        error={editProfile.error}
        onClose={editProfile.close}
        onSave={handleEditSave}
      />
      <ChangePasswordModal
        isOpen={changePassword.isOpen}
        saving={changePassword.saving}
        error={changePassword.error}
        onClose={changePassword.close}
        onSave={handlePasswordSave}
      />
    </div>
  );
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
        {label}
      </dt>
      <dd
        className={
          "mt-0.5 text-gray-900 dark:text-gray-100 " +
          (mono ? "font-mono text-xs" : "")
        }
      >
        {value}
      </dd>
    </div>
  );
}
