"use client";

import { useState, useRef, useEffect } from "react";
import axios from "axios";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}

// === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
const AGENTS = [
  { id: "general", label: "General", hint: "Ask me anything — I can search the web or check the time." },
  { id: "stock",   label: "Stock Analysis", hint: 'Try: "Analyse AAPL" · "Forecast TSLA for 6 months" · "Compare AAPL and MSFT"' },
];
// === END STOCK AGENT ROUTING ===

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  // === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
  const [agentId, setAgentId] = useState("general");
  // === END STOCK AGENT ROUTING ===

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const sendMessage = async () => {
    if (!input.trim() || loading) return;

    const userMessage: Message = {
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
    };

    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setInput("");
    setLoading(true);

    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    try {
      const res = await axios.post("http://127.0.0.1:8181/chat", {
        message: userMessage.content,
        history: messages.map((m) => ({ role: m.role, content: m.content })),
        agent_id: agentId, // === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
      });

      setMessages([
        ...updatedMessages,
        {
          role: "assistant",
          content: res.data.response,
          timestamp: new Date(),
        },
      ]);
    } catch {
      setMessages([
        ...updatedMessages,
        {
          role: "assistant",
          content: "Error connecting to server. Is the backend running?",
          timestamp: new Date(),
        },
      ]);
    }

    setLoading(false);
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // Auto-grow textarea
  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
  };

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans">

      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-gray-200 shadow-sm">
        <div className="flex items-center gap-3">
          {/* Claude sparkle icon */}
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-sm font-bold select-none">
            ✦
          </div>
          <div>
            <h1 className="font-semibold text-gray-900 leading-tight">AI Agent</h1>
            <span className="text-xs text-indigo-600 font-medium">Claude Sonnet 4.6</span>
          </div>
          {/* === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 === */}
          <div className="flex items-center gap-1 ml-4 bg-gray-100 rounded-lg p-0.5">
            {AGENTS.map((a) => (
              <button
                key={a.id}
                onClick={() => { setAgentId(a.id); setMessages([]); }}
                className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${
                  agentId === a.id
                    ? "bg-white text-indigo-700 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                {a.label}
              </button>
            ))}
          </div>
          {/* === END STOCK AGENT ROUTING === */}
        </div>

        {messages.length > 0 && (
          <button
            onClick={() => setMessages([])}
            title="Clear chat"
            className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-red-500 transition-colors px-3 py-1.5 rounded-lg hover:bg-red-50"
          >
            {/* Trash icon */}
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              <path d="M10 11v6M14 11v6" />
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
            </svg>
            Clear
          </button>
        )}
      </header>

      {/* Chat area */}
      <main className="flex-1 overflow-y-auto px-4 py-6 space-y-6">

        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center h-full text-center gap-4 pb-24">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-2xl shadow-lg">
              ✦
            </div>
            <div>
              <p className="text-gray-700 font-medium text-lg">How can I help you today?</p>
              {/* === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 === */}
              <p className="text-gray-400 text-sm mt-1">
                {AGENTS.find((a) => a.id === agentId)?.hint}
              </p>
              {/* === END STOCK AGENT ROUTING === */}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex items-end gap-2.5 ${msg.role === "user" ? "flex-row-reverse" : "flex-row"}`}
          >
            {/* Avatar */}
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
                className={`px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words shadow-sm ${
                  msg.role === "user"
                    ? "bg-indigo-600 text-white rounded-br-sm"
                    : "bg-white text-gray-800 border border-gray-100 rounded-bl-sm"
                }`}
              >
                {msg.content}
              </div>
              <span className="text-[11px] text-gray-400 px-1">
                {formatTime(msg.timestamp)}
              </span>
            </div>
          </div>
        ))}

        {/* Typing indicator */}
        {loading && (
          <div className="flex items-end gap-2.5">
            <div className="w-8 h-8 shrink-0 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-xs font-bold">
              ✦
            </div>
            <div className="bg-white border border-gray-100 rounded-2xl rounded-bl-sm shadow-sm">
              <TypingDots />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </main>

      {/* Input area */}
      <footer className="bg-white border-t border-gray-200 px-4 py-3">
        <div className="max-w-3xl mx-auto flex items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={1}
            placeholder="Message Claude..."
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            disabled={loading}
            className="flex-1 resize-none bg-gray-50 border border-gray-200 rounded-xl px-4 py-2.5 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent transition disabled:opacity-50 max-h-40 overflow-y-auto"
            style={{ height: "42px" }}
          />
          <button
            onClick={sendMessage}
            disabled={loading || !input.trim()}
            className="shrink-0 w-10 h-10 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-200 disabled:cursor-not-allowed text-white flex items-center justify-center transition-colors shadow-sm"
            title="Send"
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

    </div>
  );
}
