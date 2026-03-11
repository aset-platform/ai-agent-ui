/**
 * Chat message input area — a growing textarea with a send button.
 *
 * Enter sends; Shift+Enter inserts a newline.  Textarea auto-grows up to 160px.
 */

import { type RefObject } from "react";

interface ChatInputProps {
  input: string;
  loading: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onInput: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
}

export function ChatInput({ input, loading, textareaRef, onInput, onKeyDown, onSend }: ChatInputProps) {
  return (
    <footer className="bg-white border-t border-gray-200 px-4 py-3 shrink-0">
      <div className="max-w-3xl mx-auto flex items-end gap-2">
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder="Message Claude..."
          value={input}
          onChange={onInput}
          onKeyDown={onKeyDown}
          disabled={loading}
          data-testid="chat-message-input"
          className="flex-1 resize-none bg-gray-50 border border-gray-200 rounded-xl px-4 py-2.5 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition disabled:opacity-50 max-h-40 overflow-y-auto"
          style={{ height: "42px" }}
        />
        <button
          onClick={onSend}
          disabled={loading || !input.trim()}
          className="shrink-0 w-10 h-10 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-200 disabled:cursor-not-allowed text-white flex items-center justify-center transition-colors shadow-sm"
          title="Send"
          data-testid="chat-send-button"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 rotate-90" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
      <p className="text-center text-[11px] text-gray-400 mt-2">
        Shift+Enter for new line · Enter to send
      </p>
    </footer>
  );
}
