"use client";
/**
 * Admin password reset modal.
 *
 * Collects new password + confirmation for a target
 * user, then delegates to the parent handler.
 */

import { useState } from "react";

interface ResetPasswordModalProps {
  isOpen: boolean;
  userName: string;
  saving: boolean;
  error: string;
  onClose: () => void;
  onSave: (newPassword: string) => Promise<void>;
}

const inputClass = `
  w-full border border-gray-300 dark:border-gray-600
  bg-white dark:bg-gray-700 rounded-lg px-3 py-2
  text-sm text-gray-800 dark:text-gray-200
  focus:outline-none focus:ring-2 focus:ring-indigo-500
`;

export function ResetPasswordModal({
  isOpen,
  userName,
  saving,
  error,
  onClose,
  onSave,
}: ResetPasswordModalProps) {
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localErr, setLocalErr] = useState("");

  const handleSave = async () => {
    setLocalErr("");
    if (pw.length < 8) {
      setLocalErr("Min 8 characters required");
      return;
    }
    if (pw !== confirm) {
      setLocalErr("Passwords do not match");
      return;
    }
    await onSave(pw);
    setPw("");
    setConfirm("");
  };

  const handleClose = () => {
    setPw("");
    setConfirm("");
    setLocalErr("");
    onClose();
  };

  if (!isOpen) return null;

  const displayErr = localErr || error;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => {
        if (e.target === e.currentTarget)
          handleClose();
      }}
    >
      <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 transition-colors">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 mb-1">
          Reset Password
        </h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
          For: {userName}
        </p>

        {displayErr && (
          <p className="text-sm text-red-500 mb-3">
            {displayErr}
          </p>
        )}

        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            New Password
          </label>
          <input
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            className={inputClass}
            placeholder="Min 8 chars, 1 digit, 1 uppercase"
          />
        </div>

        <div className="mb-5">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Confirm Password
          </label>
          <input
            type="password"
            value={confirm}
            onChange={(e) =>
              setConfirm(e.target.value)
            }
            className={inputClass}
            placeholder="Repeat new password"
          />
        </div>

        <div className="flex justify-end gap-2">
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
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50"
          >
            {saving
              ? "Resetting\u2026"
              : "Reset Password"}
          </button>
        </div>
      </div>
    </div>
  );
}
