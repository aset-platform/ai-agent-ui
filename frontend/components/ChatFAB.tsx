"use client";
/**
 * Floating action button that opens the chat side panel.
 *
 * Hidden when the panel is already open. Desktop only — on mobile
 * the chat icon in ``AppHeader`` serves this purpose.
 */

import { useChatContext } from "@/providers/ChatProvider";

export function ChatFAB() {
  const { isOpen, togglePanel } = useChatContext();

  if (isOpen) return null;

  return (
    <button
      onClick={togglePanel}
      title="Open chat"
      aria-label="Open chat assistant"
      data-testid="chat-fab"
      className="fixed bottom-6 right-6 z-40 hidden md:flex w-13 h-13 rounded-full bg-gradient-to-br from-fuchsia-500 to-violet-600 items-center justify-center text-white shadow-lg hover:shadow-xl hover:scale-105 transition-all animate-in fade-in zoom-in duration-400"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className="w-6 h-6"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    </button>
  );
}
