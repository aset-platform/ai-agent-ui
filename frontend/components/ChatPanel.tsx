"use client";
/**
 * Resizable chat side panel.
 *
 * Slides in from the right when opened via the FAB (desktop)
 * or header icon (mobile). Composes the existing ``MessageBubble``
 * and ``ChatInput`` components inside a panel with drag-to-resize.
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { useChatContext } from "@/providers/ChatProvider";
import { useSendMessage } from "@/hooks/useSendMessage";
import { useResizePanel } from "@/hooks/useResizePanel";
import { CHAT_HINT } from "@/lib/constants";
import { StatusBadge } from "@/components/StatusBadge";
import { MessageBubble } from "@/components/MessageBubble";
import { ChatInput } from "@/components/ChatInput";
import { ChatPanelHeader } from "@/components/ChatPanelHeader";
import { PastSessionsTab } from "@/components/PastSessionsTab";
import { ResizeHandle } from "@/components/ResizeHandle";
import { DOCS_URL } from "@/lib/config";

const MIN_WIDTH = 320;
const MAX_WIDTH_PCT = 0.8;

export function ChatPanel() {
  const {
    messages,
    setMessages,
    isOpen,
    closePanel,
    agentId,
    sessionId,
    ws,
    startFromSession,
  } = useChatContext();

  const [activeTab, setActiveTab] = useState<
    "chat" | "history"
  >("chat");

  const handleStartFromSession = useCallback(
    (oldSessionId: string, preview: string) => {
      startFromSession(oldSessionId, preview);
      setActiveTab("chat");
    },
    [startFromSession],
  );

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [statusLine, setStatusLine] = useState("");
  const [quotaExceeded, setQuotaExceeded] =
    useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Compute max width from viewport
  const maxWidth =
    typeof window !== "undefined"
      ? window.innerWidth * MAX_WIDTH_PCT
      : 800;

  const defaultWidth =
    typeof window !== "undefined"
      ? Math.max(MIN_WIDTH, window.innerWidth * 0.45)
      : 600;

  const { width, onMouseDown } = useResizePanel(
    MIN_WIDTH,
    maxWidth,
    defaultWidth,
  );

  const { sendMessage, sendDirect, handleKeyDown, handleInput } =
    useSendMessage({
      agentId,
      sessionId,
      messages,
      setMessages,
      setLoading,
      setStatusLine,
      input,
      setInput,
      textareaRef,
      ws,
    });

  // Scroll to bottom on new messages / status changes.
  // Uses scrollTop on the container which is more
  // reliable than scrollIntoView in React 19.
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const id = setTimeout(() => {
      el.scrollTop = el.scrollHeight;
    }, 30);
    return () => clearTimeout(id);
  }, [messages, loading, statusLine]);

  // Re-focus textarea when loading finishes (the
  // textarea was disabled during loading, which
  // drops browser focus).
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    if (prevLoadingRef.current && !loading) {
      setTimeout(
        () => textareaRef.current?.focus(),
        100,
      );
    }
    prevLoadingRef.current = loading;
  }, [loading]);

  // Detect quota exceeded from error messages
  useEffect(() => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (
      last.role === "assistant" &&
      typeof last.content === "string" &&
      last.content.toLowerCase().includes(
        "quota exceeded",
      )
    ) {
      /* eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: detecting error state from message */
      setQuotaExceeded(true);
    }
  }, [messages]);

  // ESC to close
  useEffect(() => {
    if (!isOpen) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePanel();
    };
    document.addEventListener("keydown", handleEsc);
    return () =>
      document.removeEventListener("keydown", handleEsc);
  }, [isOpen, closePanel]);

  // Focus textarea when panel opens.
  // Retry twice — the panel animation (300ms) and React
  // concurrent rendering can delay textarea mount.
  useEffect(() => {
    if (isOpen) {
      const tryFocus = (delay: number) =>
        setTimeout(() => {
          textareaRef.current?.focus();
        }, delay);
      const t1 = tryFocus(350);
      const t2 = tryFocus(600);
      return () => {
        clearTimeout(t1);
        clearTimeout(t2);
      };
    }
  }, [isOpen]);

  const agentHint = CHAT_HINT;

  // Internal link handler for markdown links
  const handleInternalLink = useCallback(
    (_href: string) => {
      // In side-panel mode, internal links open in
      // a new tab since we can't switch iframe views
      if (
        _href.startsWith(DOCS_URL)
      ) {
        window.open(_href, "_blank");
      }
    },
    [],
  );

  return (
    <>
      {/* Desktop panel */}
      <div
        className={`hidden md:flex flex-col fixed top-14 right-0 bottom-0 z-30 bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-xl transition-transform duration-300 ease-in-out ${
          isOpen
            ? "translate-x-0"
            : "translate-x-full"
        }`}
        style={{ width: isOpen ? width : 0 }}
        data-testid="chat-panel"
      >
        {isOpen && (
          <>
            <ResizeHandle onMouseDown={onMouseDown} />
            <ChatPanelHeader
              activeTab={activeTab}
              onTabChange={setActiveTab}
            />

            {activeTab === "chat" ? (
              <>
                {/* Messages */}
                <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
                  {messages.length === 0 && !loading && (
                    <div className="flex flex-col items-center justify-center h-full text-center gap-3">
                      <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-lg shadow-md">
                        ✦
                      </div>
                      <div>
                        <p className="text-gray-700 dark:text-gray-200 font-medium text-sm">
                          How can I help?
                        </p>
                        <p className="text-gray-400 dark:text-gray-500 text-xs mt-1">
                          {agentHint}
                        </p>
                      </div>
                    </div>
                  )}

                  {messages.map((msg, i) => (
                    <MessageBubble
                      key={`${msg.timestamp.getTime()}-${msg.role}-${i}`}
                      message={msg}
                      onInternalLink={handleInternalLink}
                      onActionClick={sendDirect}
                    />
                  ))}

                  {loading && (
                    <div className="flex items-end gap-2">
                      <div className="w-7 h-7 shrink-0 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-xs font-bold">
                        ✦
                      </div>
                      <div className="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-2xl rounded-bl-sm shadow-sm">
                        <StatusBadge
                          text={statusLine || "Thinking..."}
                        />
                      </div>
                    </div>
                  )}

                  <div ref={messagesEndRef} />
                </div>

                <ChatInput
                  input={input}
                  loading={loading}
                  quotaExceeded={quotaExceeded}
                  onUpgrade={() => {
                    closePanel();
                    window.dispatchEvent(
                      new CustomEvent("open-billing"),
                    );
                  }}
                  textareaRef={textareaRef}
                  onInput={handleInput}
                  onKeyDown={handleKeyDown}
                  onSend={sendMessage}
                />
              </>
            ) : (
              <PastSessionsTab
                onStartFromSession={
                  handleStartFromSession
                }
              />
            )}
          </>
        )}
      </div>

      {/* Mobile full-screen overlay */}
      {isOpen && (
        <div
          className="md:hidden fixed inset-0 z-50 flex flex-col bg-white dark:bg-gray-900"
          data-testid="chat-panel-mobile"
        >
          <ChatPanelHeader
            activeTab={activeTab}
            onTabChange={setActiveTab}
          />

          {activeTab === "chat" ? (
            <>
              <div className="flex-1 overflow-y-auto px-3 py-4 space-y-4">
                {messages.length === 0 && !loading && (
                  <div className="flex flex-col items-center justify-center h-full text-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-lg shadow-md">
                      ✦
                    </div>
                    <p className="text-gray-700 dark:text-gray-200 font-medium text-sm">
                      How can I help?
                    </p>
                  </div>
                )}

                {messages.map((msg, i) => (
                  <MessageBubble
                    key={`${msg.timestamp.getTime()}-${msg.role}-${i}`}
                    message={msg}
                    onInternalLink={handleInternalLink}
                    onActionClick={sendDirect}
                  />
                ))}

                {loading && (
                  <div className="flex items-end gap-2">
                    <div className="w-7 h-7 shrink-0 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-xs font-bold">
                      ✦
                    </div>
                    <div className="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-2xl rounded-bl-sm shadow-sm">
                      <StatusBadge
                        text={statusLine || "Thinking..."}
                      />
                    </div>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>

              <ChatInput
                input={input}
                loading={loading}
                textareaRef={textareaRef}
                onInput={handleInput}
                onKeyDown={handleKeyDown}
                onSend={sendMessage}
              />
            </>
          ) : (
            <PastSessionsTab />
          )}
        </div>
      )}
    </>
  );
}
