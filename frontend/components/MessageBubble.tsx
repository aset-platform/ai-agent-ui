/**
 * Renders a single chat message bubble for either the user or the assistant.
 *
 * User messages are displayed right-aligned with an indigo background.
 * Assistant messages are left-aligned with rendered Markdown.
 */

import React from "react";
import dynamic from "next/dynamic";
import { ActionButtons } from "./ActionButtons";
import { formatTime } from "@/lib/constants";
import type { Message } from "@/lib/constants";

// MarkdownContent transitively pulls react-markdown +
// remark-gfm (~105 KB pre-gzip). Dashboard loads
// MessageBubble for the chat panel but the panel is
// collapsed by default — defer the chunk so its
// parse cost doesn't show up in initial dashboard
// LCP. (ASETPLTFRM-334 phase C)
const MarkdownContent = dynamic(
  () =>
    import("./MarkdownContent").then(
      (m) => m.MarkdownContent,
    ),
  {
    ssr: false,
    loading: () => (
      <div className="text-sm text-gray-400">…</div>
    ),
  },
);

interface MessageBubbleProps {
  message: Message;
  onInternalLink: (href: string) => void;
  onActionClick?: (prompt: string) => void;
}

export const MessageBubble = React.memo(function MessageBubble({ message: msg, onInternalLink, onActionClick }: MessageBubbleProps) {
  return (
    <div className={`flex items-end gap-2.5 ${msg.role === "user" ? "flex-row-reverse" : "flex-row"}`} data-testid={msg.role === "user" ? "user-message" : "assistant-message"}>
      {msg.role === "assistant" ? (
        <div className="w-8 h-8 shrink-0 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-xs font-bold select-none">
          ✦
        </div>
      ) : (
        <div className="w-8 h-8 shrink-0 rounded-full bg-indigo-100 dark:bg-indigo-900/50 flex items-center justify-center text-indigo-700 dark:text-indigo-300 text-xs font-bold select-none">
          You
        </div>
      )}

      <div className={`flex flex-col gap-1 max-w-[85%] md:max-w-[72%] ${msg.role === "user" ? "items-end" : "items-start"}`}>
        <div
          className={`px-4 py-2.5 rounded-2xl text-sm shadow-sm ${
            msg.role === "user"
              ? "bg-indigo-600 text-white rounded-br-sm leading-relaxed whitespace-pre-wrap break-words"
              : "bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 border border-gray-100 dark:border-gray-700 rounded-bl-sm"
          }`}
        >
          {msg.role === "user" ? (
            msg.content
          ) : (
            <>
              <MarkdownContent content={msg.content} onInternalLink={onInternalLink} />
              {msg.actions && msg.actions.length > 0 && onActionClick && (
                <ActionButtons actions={msg.actions} onAction={onActionClick} />
              )}
            </>
          )}
        </div>
        <div className="flex items-center gap-1.5 px-1">
          <span className="text-[11px] text-gray-400 dark:text-gray-500">
            {formatTime(msg.timestamp)}
          </span>
          {msg.role === "assistant" &&
            msg.memoryUsed && (
              <span className="text-[10px] text-violet-500 dark:text-violet-400">
                memory
              </span>
            )}
        </div>
      </div>
    </div>
  );
});
