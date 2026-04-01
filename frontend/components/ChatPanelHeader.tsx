"use client";
/**
 * Header bar for the chat side panel.
 *
 * Shows the title, agent switcher pills, and a close button.
 * Tabs switch between live chat and past sessions history.
 */

import { useChatContext } from "@/providers/ChatProvider";

interface ChatPanelHeaderProps {
  activeTab: "chat" | "history";
  onTabChange: (tab: "chat" | "history") => void;
}

export function ChatPanelHeader({
  activeTab,
  onTabChange,
}: ChatPanelHeaderProps) {
  const { closePanel } = useChatContext();

  return (
    <div className="border-b border-gray-200 dark:border-gray-700 px-4 py-3 shrink-0">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          AI Assistant
        </h2>
        <button
          onClick={closePanel}
          data-testid="chat-panel-close"
          className="w-8 h-8 flex items-center justify-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          aria-label="Close chat"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="w-4 h-4"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      {/* Tabs: Chat | Past Sessions */}
      <div className="flex gap-4 text-xs">
        <button
          onClick={() => onTabChange("chat")}
          className={`pb-1 font-medium transition-colors ${
            activeTab === "chat"
              ? "text-indigo-600 dark:text-indigo-400 border-b-2 border-indigo-600 dark:border-indigo-400"
              : "text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
          }`}
        >
          Chat
        </button>
        <button
          onClick={() => onTabChange("history")}
          className={`pb-1 font-medium transition-colors ${
            activeTab === "history"
              ? "text-indigo-600 dark:text-indigo-400 border-b-2 border-indigo-600 dark:border-indigo-400"
              : "text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
          }`}
        >
          Past Sessions
        </button>
      </div>
    </div>
  );
}
