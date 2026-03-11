/**
 * Renders a single chat message bubble for either the user or the assistant.
 *
 * User messages are displayed right-aligned with an indigo background.
 * Assistant messages are left-aligned with rendered Markdown.
 */

import React from "react";
import { MarkdownContent } from "./MarkdownContent";
import { formatTime } from "@/lib/constants";
import type { Message } from "@/lib/constants";

interface MessageBubbleProps {
  message: Message;
  onInternalLink: (href: string) => void;
}

export const MessageBubble = React.memo(function MessageBubble({ message: msg, onInternalLink }: MessageBubbleProps) {
  return (
    <div className={`flex items-end gap-2.5 ${msg.role === "user" ? "flex-row-reverse" : "flex-row"}`} data-testid={msg.role === "user" ? "user-message" : "assistant-message"}>
      {msg.role === "assistant" ? (
        <div className="w-8 h-8 shrink-0 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-xs font-bold select-none">
          ✦
        </div>
      ) : (
        <div className="w-8 h-8 shrink-0 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 text-xs font-bold select-none">
          You
        </div>
      )}

      <div className={`flex flex-col gap-1 max-w-[72%] ${msg.role === "user" ? "items-end" : "items-start"}`}>
        <div
          className={`px-4 py-2.5 rounded-2xl text-sm shadow-sm ${
            msg.role === "user"
              ? "bg-indigo-600 text-white rounded-br-sm leading-relaxed whitespace-pre-wrap break-words"
              : "bg-white text-gray-800 border border-gray-100 rounded-bl-sm"
          }`}
        >
          {msg.role === "user" ? (
            msg.content
          ) : (
            <MarkdownContent content={msg.content} onInternalLink={onInternalLink} />
          )}
        </div>
        <span className="text-[11px] text-gray-400 px-1">{formatTime(msg.timestamp)}</span>
      </div>
    </div>
  );
});
