/**
 * Renders clickable action buttons below assistant messages.
 *
 * Used for interactive stock discovery — each button injects
 * a prompt into the chat input and auto-submits.
 */

import React from "react";
import type { ActionButton } from "@/lib/constants";

interface ActionButtonsProps {
  actions: ActionButton[];
  onAction: (prompt: string) => void;
  disabled?: boolean;
}

export const ActionButtons = React.memo(
  function ActionButtons({
    actions,
    onAction,
    disabled,
  }: ActionButtonsProps) {
    if (!actions.length) return null;

    return (
      <div className="flex flex-wrap gap-2 mt-2">
        {actions.map((action, i) => (
          <button
            key={i}
            onClick={() => onAction(action.prompt)}
            disabled={disabled}
            className="inline-flex items-center gap-1.5
              px-3 py-1.5 rounded-full text-xs font-medium
              bg-indigo-50 dark:bg-indigo-900/30
              text-indigo-700 dark:text-indigo-300
              border border-indigo-200 dark:border-indigo-700
              hover:bg-indigo-100 dark:hover:bg-indigo-900/50
              transition-colors cursor-pointer
              disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {action.label}
            <svg
              className="w-3 h-3"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </button>
        ))}
      </div>
    );
  },
);
