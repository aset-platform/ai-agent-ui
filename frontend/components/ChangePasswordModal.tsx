/**
 * Change Password modal.
 *
 * Collects new password + confirmation, then delegates to the
 * `useChangePassword` hook which runs the two-step backend reset flow.
 */

import { useState } from "react";

interface ChangePasswordModalProps {
  isOpen: boolean;
  saving: boolean;
  error: string;
  onClose: () => void;
  onSave: (newPassword: string, confirmPassword: string) => Promise<void>;
}

export function ChangePasswordModal({
  isOpen,
  saving,
  error,
  onClose,
  onSave,
}: ChangePasswordModalProps) {
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");

  const handleSave = async () => {
    await onSave(newPw, confirmPw);
    setNewPw("");
    setConfirmPw("");
  };

  const handleClose = () => {
    setNewPw("");
    setConfirmPw("");
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}
    >
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6">
        <h2 className="text-base font-semibold text-gray-900 mb-4">Change Password</h2>

        {error && (
          <p className="text-sm text-red-500 mb-3">{error}</p>
        )}

        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">New Password</label>
          <input
            type="password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="Min 8 chars, at least one digit"
          />
        </div>

        <div className="mb-5">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Confirm New Password
          </label>
          <input
            type="password"
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="Repeat new password"
          />
        </div>

        <div className="flex justify-end gap-2">
          <button
            onClick={handleClose}
            disabled={saving}
            className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
