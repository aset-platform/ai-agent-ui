"use client";
/**
 * Reusable confirmation dialog for destructive actions.
 *
 * Renders a modal overlay with title, message, and
 * Cancel / Confirm buttons. Dismisses on Escape and
 * backdrop click. Auto-focuses the confirm button.
 */

import { useEffect, useRef } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "warning";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  variant = "danger",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Focus confirm button on open
  useEffect(() => {
    if (open) confirmRef.current?.focus();
  }, [open]);

  // Escape key dismisses
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handler);
    return () =>
      document.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  const confirmColors =
    variant === "danger"
      ? "bg-red-600 hover:bg-red-700 focus:ring-red-500"
      : "bg-amber-500 hover:bg-amber-600 focus:ring-amber-400";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
      data-testid="confirm-dialog"
      onClick={onCancel}
    >
      <div
        className="
          w-full max-w-sm mx-4
          rounded-2xl shadow-xl
          bg-white dark:bg-gray-800
          border border-gray-200 dark:border-gray-700
          p-6
        "
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          {title}
        </h3>
        <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
          {message}
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            data-testid="confirm-dialog-cancel"
            className="
              rounded-lg px-4 py-2 text-sm
              font-medium transition-colors
              border border-gray-300 dark:border-gray-600
              text-gray-700 dark:text-gray-300
              hover:bg-gray-100 dark:hover:bg-gray-700
              focus:outline-none focus:ring-2
              focus:ring-gray-400/50
            "
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            data-testid="confirm-dialog-confirm"
            className={`
              rounded-lg px-4 py-2 text-sm
              font-medium text-white
              transition-colors
              focus:outline-none focus:ring-2
              ${confirmColors}
            `}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
