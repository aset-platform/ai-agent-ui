/**
 * Chat message input area — a growing textarea with a send button.
 *
 * Enter sends; Shift+Enter inserts a newline.  Textarea auto-grows up to 160px.
 * When quota is exceeded, input is disabled and an upgrade prompt is shown.
 */

import { type RefObject } from "react";

interface ChatInputProps {
  input: string;
  loading: boolean;
  quotaExceeded?: boolean;
  onUpgrade?: () => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onInput: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
}

export function ChatInput({
  input,
  loading,
  quotaExceeded,
  onUpgrade,
  textareaRef,
  onInput,
  onKeyDown,
  onSend,
}: ChatInputProps) {
  // Use readOnly (not disabled) during loading so
  // the textarea retains browser focus.  Only truly
  // disable for quotaExceeded.
  const hardDisabled = !!quotaExceeded;
  const blocked = loading || hardDisabled;

  return (
    <footer className="bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 px-3 md:px-4 py-2 md:py-3 shrink-0 transition-colors">
      {quotaExceeded && (
        <div className="max-w-3xl mx-auto mb-2 flex items-center justify-between gap-2 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-3 py-2">
          <p className="text-xs text-amber-800 dark:text-amber-200">
            Monthly analysis quota exceeded.
          </p>
          {onUpgrade && (
            <button
              onClick={onUpgrade}
              className="shrink-0 text-xs font-medium px-3 py-1 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors"
            >
              Upgrade Plan
            </button>
          )}
        </div>
      )}
      <div className="max-w-3xl mx-auto flex items-end gap-2">
        <textarea
          ref={textareaRef}
          rows={1}
          autoFocus
          placeholder={quotaExceeded ? "Upgrade your plan to continue..." : "Message Claude..."}
          value={input}
          onChange={onInput}
          onKeyDown={onKeyDown}
          readOnly={loading}
          disabled={hardDisabled}
          data-testid="chat-message-input"
          className={`flex-1 resize-none bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl px-3 md:px-4 py-2.5 text-sm text-gray-800 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:focus:ring-indigo-500 focus:border-transparent transition max-h-40 overflow-y-auto${loading ? " opacity-50" : ""}${hardDisabled ? " disabled:opacity-50" : ""}`}
          style={{ height: "42px" }}
        />
        <button
          onClick={onSend}
          disabled={blocked || !input.trim()}
          className="shrink-0 w-11 h-11 md:w-10 md:h-10 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-200 dark:disabled:bg-gray-700 disabled:cursor-not-allowed text-white flex items-center justify-center transition-colors shadow-sm"
          title="Send"
          data-testid="chat-send-button"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 rotate-90" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
      <p className="text-center text-[11px] text-gray-400 dark:text-gray-500 mt-1.5 md:mt-2 hidden md:block">
        {quotaExceeded ? "Upgrade your plan for more analyses" : "Shift+Enter for new line \u00b7 Enter to send"}
      </p>
    </footer>
  );
}
