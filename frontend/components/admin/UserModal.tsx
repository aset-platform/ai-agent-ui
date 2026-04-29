"use client";
/**
 * Add / Edit user modal for the Admin page.
 *
 * In "add" mode: shows password field, hides active toggle.
 * In "edit" mode: hides password, shows active toggle +
 * page permissions for non-superuser roles.
 */

import { useState } from "react";
import type { UserResponse } from "@/lib/types";

export interface UserFormData {
  full_name: string;
  email: string;
  password: string;
  role: "superuser" | "pro" | "general";
  is_active: boolean;
  page_permissions: Record<string, boolean>;
}

interface UserModalProps {
  mode: "add" | "edit";
  user: UserResponse | null;
  saving: boolean;
  error: string;
  onClose: () => void;
  onSave: (data: UserFormData) => Promise<void>;
}

const inputClass = `
  w-full border border-gray-300 dark:border-gray-600
  bg-white dark:bg-gray-700 rounded-lg px-3 py-2
  text-sm text-gray-800 dark:text-gray-200
  focus:outline-none focus:ring-2 focus:ring-indigo-500
`;

const labelClass =
  "block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1";

export function UserModal({
  mode,
  user,
  saving,
  error,
  onClose,
  onSave,
}: UserModalProps) {
  // Lazy-init form state from props on mount. Parent
  // controls visibility by mounting the modal only when
  // open + keying it on user identity, so the form
  // resets cleanly without a setState-in-effect cascade.
  const isEdit = mode === "edit" && user !== null;
  const [name, setName] = useState(
    isEdit ? user.full_name : "",
  );
  const [email, setEmail] = useState(
    isEdit ? user.email : "",
  );
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<
    "superuser" | "pro" | "general"
  >(
    isEdit
      ? (user.role as "superuser" | "pro" | "general")
      : "general",
  );
  const [active, setActive] = useState(
    isEdit ? user.is_active : true,
  );
  const [perms, setPerms] = useState<
    Record<string, boolean>
  >(
    isEdit
      ? (user.page_permissions ?? {
          insights: false,
          admin: false,
        })
      : { insights: false, admin: false },
  );

  const handleSave = () =>
    onSave({
      full_name: name,
      email,
      password,
      role,
      is_active: active,
      page_permissions: perms,
    });

  const handleClose = () => {
    setPassword("");
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => {
        if (e.target === e.currentTarget)
          handleClose();
      }}
    >
      <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xl w-full max-w-md mx-4 p-6 transition-colors max-h-[90vh] overflow-y-auto">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 mb-4">
          {mode === "add"
            ? "Add User"
            : "Edit User"}
        </h2>

        {error && (
          <p className="text-sm text-red-500 mb-3">
            {error}
          </p>
        )}

        {/* Full Name */}
        <div className="mb-3">
          <label className={labelClass}>
            Full Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) =>
              setName(e.target.value)
            }
            data-testid="user-modal-name"
            className={inputClass}
            placeholder="Jane Doe"
          />
        </div>

        {/* Email */}
        <div className="mb-3">
          <label className={labelClass}>Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) =>
              setEmail(e.target.value)
            }
            data-testid="user-modal-email"
            className={inputClass}
            placeholder="jane@example.com"
          />
        </div>

        {/* Password (add mode only) */}
        {mode === "add" && (
          <div className="mb-3">
            <label className={labelClass}>
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) =>
                setPassword(e.target.value)
              }
              data-testid="user-modal-password"
              className={inputClass}
              placeholder="Min 8 chars, 1 digit, 1 uppercase"
            />
          </div>
        )}

        {/* Role */}
        <div className="mb-3">
          <label className={labelClass}>Role</label>
          <select
            value={role}
            onChange={(e) =>
              setRole(
                e.target.value as
                  | "superuser"
                  | "pro"
                  | "general",
              )
            }
            data-testid="user-modal-role"
            className={inputClass}
          >
            <option value="general">
              General User (free tier)
            </option>
            <option value="pro">
              Pro User (paid tier)
            </option>
            <option value="superuser">
              Superuser
            </option>
          </select>
          <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
            Pro and general are auto-synced from
            subscription tier on webhook events.
          </p>
        </div>

        {/* Page Permissions (general role only) */}
        {role === "general" && (
          <div className="mb-3">
            <label className={labelClass}>
              Page Access
            </label>
            <div className="space-y-2 mt-1">
              {["insights", "admin"].map((key) => (
                <label
                  key={key}
                  className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"
                >
                  <input
                    type="checkbox"
                    checked={perms[key] ?? false}
                    onChange={(e) =>
                      setPerms((p) => ({
                        ...p,
                        [key]: e.target.checked,
                      }))
                    }
                    className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500"
                  />
                  {key.charAt(0).toUpperCase() +
                    key.slice(1)}
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Active toggle (edit mode only) */}
        {mode === "edit" && (
          <div className="mb-4">
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={active}
                onChange={(e) =>
                  setActive(e.target.checked)
                }
                className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500"
              />
              Active account
            </label>
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={handleClose}
            disabled={saving}
            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            data-testid="user-modal-submit"
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving
              ? "Saving\u2026"
              : mode === "add"
                ? "Create"
                : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
