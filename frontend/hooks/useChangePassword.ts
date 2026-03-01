"use client";
/**
 * Hook encapsulating the Change Password modal state and save logic.
 *
 * Exposes `isOpen`, `open`, `close`, `save` (async — runs the two-step
 * password-reset flow), `saving`, and `error`.
 */

import { useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8181";

interface UseChangePasswordResult {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  saving: boolean;
  error: string;
  save: (email: string, newPassword: string, confirmPassword: string) => Promise<boolean>;
}

export function useChangePassword(): UseChangePasswordResult {
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

  const save = async (
    email: string,
    newPassword: string,
    confirmPassword: string,
  ): Promise<boolean> => {
    // Client-side validation
    if (!newPassword) {
      setError("New password is required.");
      return false;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return false;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return false;
    }
    if (!/\d/.test(newPassword)) {
      setError("Password must contain at least one digit.");
      return false;
    }

    setSaving(true);
    setError("");
    try {
      // Step 1: request a reset token
      const reqRes = await apiFetch(`${BACKEND_URL}/auth/password-reset/request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!reqRes.ok) {
        const detail = (await reqRes.json().catch(() => ({}))).detail ?? "Request failed.";
        setError(String(detail));
        return false;
      }
      const { reset_token } = (await reqRes.json()) as { reset_token: string };
      if (!reset_token) {
        setError("No reset token returned by server.");
        return false;
      }

      // Step 2: confirm with new password
      const confRes = await apiFetch(`${BACKEND_URL}/auth/password-reset/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reset_token, new_password: newPassword }),
      });
      if (!confRes.ok) {
        const detail = (await confRes.json().catch(() => ({}))).detail ?? "Confirm failed.";
        setError(String(detail));
        return false;
      }

      setIsOpen(false);
      return true;
    } catch {
      setError("Network error — please try again.");
      return false;
    } finally {
      setSaving(false);
    }
  };

  return { isOpen, open, close, saving, error, save };
}
