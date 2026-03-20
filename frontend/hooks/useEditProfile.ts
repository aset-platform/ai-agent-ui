"use client";
/**
 * Hook encapsulating the Edit Profile modal state and save logic.
 *
 * Exposes `isOpen`, `open`, `close`, `save` (async — calls PATCH /auth/me
 * and optionally POST /auth/upload-avatar), `saving`, and `error`.
 */

import { useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export interface UserProfile {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  avatar_url?: string | null;
  page_permissions?: Record<string, boolean> | null;
}

interface UseEditProfileResult {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  saving: boolean;
  error: string;
  save: (fullName: string, avatarFile: File | null) => Promise<UserProfile | null>;
}

export function useEditProfile(): UseEditProfileResult {
  const [isOpen, setIsOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const open = () => {
    setError("");
    setIsOpen(true);
  };

  const close = () => {
    setError("");
    setIsOpen(false);
  };

  const save = async (fullName: string, avatarFile: File | null): Promise<UserProfile | null> => {
    if (!fullName.trim()) {
      setError("Full name is required.");
      return null;
    }
    setSaving(true);
    setError("");
    try {
      // 1. Update display name via PATCH /auth/me
      const patchRes = await apiFetch(`${API_URL}/auth/me`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ full_name: fullName.trim() }),
      });
      if (!patchRes.ok) {
        const detail = (await patchRes.json().catch(() => ({}))).detail ?? "Save failed.";
        setError(String(detail));
        return null;
      }
      let updated: UserProfile = await patchRes.json();

      // 2. Upload avatar if provided
      if (avatarFile) {
        const formData = new FormData();
        formData.append("file", avatarFile, avatarFile.name);
        const uploadRes = await apiFetch(`${API_URL}/auth/upload-avatar`, {
          method: "POST",
          body: formData,
        });
        if (uploadRes.ok) {
          const { avatar_url } = (await uploadRes.json()) as { avatar_url: string };
          updated = { ...updated, avatar_url };
        }
      }

      setIsOpen(false);
      return updated;
    } catch {
      setError("Network error — please try again.");
      return null;
    } finally {
      setSaving(false);
    }
  };

  return { isOpen, open, close, saving, error, save };
}
