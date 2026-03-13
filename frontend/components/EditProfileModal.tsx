/**
 * Edit Profile modal.
 *
 * Lets the user update their display name and optionally upload a new
 * avatar image (≤ 10 MB, enforced client-side before submitting).
 * The form pre-populates from the current profile whenever the modal opens.
 */

import { useState, useRef, useEffect, useCallback, type ChangeEvent } from "react";
import Image from "next/image";
import type { UserProfile } from "@/hooks/useEditProfile";

import { BACKEND_URL } from "@/lib/config";

const MAX_BYTES = 10 * 1024 * 1024; // 10 MB
const UNSUPPORTED_TYPES = ["image/heic", "image/heif", "image/tiff", "image/bmp"];

interface EditProfileModalProps {
  isOpen: boolean;
  profile: UserProfile | null;
  saving: boolean;
  error: string;
  onClose: () => void;
  onSave: (fullName: string, avatarFile: File | null) => Promise<void>;
}

export function EditProfileModal({
  isOpen,
  profile,
  saving,
  error,
  onClose,
  onSave,
}: EditProfileModalProps) {
  const [name, setName] = useState(profile?.full_name ?? "");
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [previewErr, setPreviewErr] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // Re-populate the form whenever the modal opens.
  useEffect(() => {
    if (isOpen) {
      /* eslint-disable react-hooks/set-state-in-effect -- resetting form state on open is intentional */
      setName(profile?.full_name ?? "");
      setAvatarFile(null);
      setPreviewSrc(null);
      setPreviewErr(false);
      setFileError("");
      /* eslint-enable react-hooks/set-state-in-effect */
      if (fileRef.current) fileRef.current.value = "";
    }
  }, [isOpen, profile?.full_name]);

  // Fix #6: revoke object URL when it changes to free memory
  useEffect(() => {
    return () => {
      if (previewSrc && previewSrc.startsWith("blob:")) {
        URL.revokeObjectURL(previewSrc);
      }
    };
  }, [previewSrc]);

  const handleFileChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    if (!file) { setAvatarFile(null); setPreviewSrc(null); return; }
    if (UNSUPPORTED_TYPES.includes(file.type.toLowerCase()) || /\.(heic|heif|tiff?|bmp)$/i.test(file.name)) {
      setFileError("Unsupported format. Please use JPEG, PNG, GIF, or WebP.");
      setAvatarFile(null);
      setPreviewSrc(null);
      return;
    }
    if (file.size > MAX_BYTES) {
      setFileError("Avatar must be under 10 MB.");
      setAvatarFile(null);
      setPreviewSrc(null);
      return;
    }
    setFileError("");
    setAvatarFile(file);
    // Fix #6: createObjectURL is non-blocking and avoids loading the full file
    // into memory as base64; the blob URL is revoked by the useEffect above.
    setPreviewSrc(URL.createObjectURL(file));
  }, []);

  const handleSave = async () => {
    await onSave(name, avatarFile);
  };

  if (!isOpen) return null;

  // Resolve the saved avatar URL — relative paths are served by the backend.
  const savedAvatarSrc = profile?.avatar_url
    ? profile.avatar_url.startsWith("/")
      ? `${BACKEND_URL}${profile.avatar_url}`
      : profile.avatar_url
    : null;

  // Show newly selected file preview, else current saved avatar (with onError fallback).
  const displaySrc = previewSrc ?? savedAvatarSrc;

  const initials = profile?.full_name
    ? profile.full_name
        .split(" ")
        .map((w) => w[0])
        .join("")
        .slice(0, 2)
        .toUpperCase()
    : "?";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6 transition-colors" data-testid="edit-profile-modal">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 mb-4">Edit Profile</h2>

        {/* Current avatar preview */}
        <div className="flex justify-center mb-4">
          {displaySrc && !previewErr ? (
            <Image
              src={displaySrc}
              alt=""
              width={64}
              height={64}
              onError={() => setPreviewErr(true)}
              className="w-16 h-16 rounded-full object-cover object-top border border-gray-200 dark:border-gray-600 shadow-sm"
              unoptimized
            />
          ) : (
            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-lg font-semibold shadow-sm">
              {initials}
            </div>
          )}
        </div>

        {error && (
          <p className="text-sm text-red-500 mb-3">{error}</p>
        )}

        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Full Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 rounded-lg px-3 py-2 text-sm text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="Your display name"
          />
        </div>

        <div className="mb-5">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Avatar (optional, max 10 MB)
          </label>
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/gif,image/webp"
            onChange={handleFileChange}
            className="block w-full text-sm text-gray-500 dark:text-gray-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-indigo-50 dark:file:bg-indigo-900/30 file:text-indigo-700 dark:file:text-indigo-400 hover:file:bg-indigo-100 dark:hover:file:bg-indigo-900/50"
          />
          {fileError && <p className="mt-1 text-xs text-red-500">{fileError}</p>}
          {avatarFile && !fileError && (
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{avatarFile.name}</p>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={saving}
            className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !!fileError}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
