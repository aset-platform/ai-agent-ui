"use client";

import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type View = "chat" | "docs" | "dashboard";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function StatusBadge({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-3">
      <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse shrink-0" />
      <span className="text-sm text-gray-500 italic">{text}</span>
    </div>
  );
}

function toolLabel(name: string): string {
  const labels: Record<string, string> = {
    fetch_stock_data:     "Fetching stock data",
    get_stock_info:       "Getting stock info",
    load_stock_data:      "Loading stock data",
    fetch_multiple_stocks:"Fetching multiple stocks",
    get_dividend_history: "Getting dividend history",
    list_available_stocks:"Listing available stocks",
    analyse_stock_price:  "Analysing price",
    forecast_stock:       "Generating forecast",
    search_market_news:   "Searching market news",
    search_web:           "Searching the web",
    get_current_time:     "Checking time",
  };
  return labels[name] ?? `Calling ${name}`;
}

function preprocessContent(content: string): string {
  const dashboardUrl = process.env.NEXT_PUBLIC_DASHBOARD_URL ?? "http://127.0.0.1:8050";

  content = content.replace(
    /\S+\/charts\/analysis\/([A-Z0-9._-]+)_analysis\.html/g,
    (_, ticker) => `[View ${ticker} Analysis →](${dashboardUrl}/analysis?ticker=${ticker})`
  );
  content = content.replace(
    /\S+\/charts\/forecasts\/([A-Z0-9._-]+)_forecast\.html/g,
    (_, ticker) => `[View ${ticker} Forecast →](${dashboardUrl}/forecast?ticker=${ticker})`
  );
  content = content.replace(/\S+\/data\/(raw|processed|forecasts|cache|metadata)\/\S+/g, "");

  return content;
}

function MarkdownContent({ content, onInternalLink }: { content: string; onInternalLink: (href: string) => void }) {
  const dashboardBase = process.env.NEXT_PUBLIC_DASHBOARD_URL ?? "http://127.0.0.1:8050";
  const docsBase = process.env.NEXT_PUBLIC_DOCS_URL ?? "http://127.0.0.1:8000";

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h1 className="text-lg font-bold mt-3 mb-1 first:mt-0">{children}</h1>,
        h2: ({ children }) => <h2 className="text-base font-semibold mt-3 mb-1 first:mt-0">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-semibold mt-2 mb-1 first:mt-0">{children}</h3>,
        p:  ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
        ul: ({ children }) => <ul className="list-disc pl-5 mb-2 space-y-0.5">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 space-y-0.5">{children}</ol>,
        li: ({ children }) => <li className="leading-relaxed">{children}</li>,
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        em:     ({ children }) => <em className="italic">{children}</em>,
        hr: () => <hr className="border-gray-200 my-3" />,
        blockquote: ({ children }) => (
          <blockquote className="border-l-4 border-indigo-300 pl-3 italic text-gray-500 my-2">{children}</blockquote>
        ),
        a: ({ href, children }) => {
          const isInternal = href && (href.startsWith(dashboardBase) || href.startsWith(docsBase));
          if (isInternal) {
            return (
              <button
                onClick={() => onInternalLink(href!)}
                className="text-indigo-600 underline hover:text-indigo-800 cursor-pointer text-left"
              >
                {children}
              </button>
            );
          }
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-indigo-600 underline hover:text-indigo-800">
              {children}
            </a>
          );
        },
        pre: ({ children }) => (
          <pre className="bg-gray-900 text-gray-100 rounded-lg px-4 py-3 overflow-x-auto text-xs font-mono my-2">
            {children}
          </pre>
        ),
        code: ({ className, children }) =>
          className ? (
            <code className="font-mono">{children}</code>
          ) : (
            <code className="bg-gray-100 text-indigo-700 rounded px-1 py-0.5 text-[0.83em] font-mono">
              {children}
            </code>
          ),
        table: ({ children }) => (
          <div className="overflow-x-auto my-2">
            <table className="min-w-full text-xs border-collapse">{children}</table>
          </div>
        ),
        thead: ({ children }) => <thead className="bg-gray-50">{children}</thead>,
        th: ({ children }) => (
          <th className="border border-gray-200 px-3 py-1.5 font-semibold text-left text-gray-700">{children}</th>
        ),
        td: ({ children }) => (
          <td className="border border-gray-200 px-3 py-1.5 text-gray-700">{children}</td>
        ),
        tr: ({ children }) => <tr className="even:bg-gray-50">{children}</tr>,
      }}
    >
      {preprocessContent(content)}
    </ReactMarkdown>
  );
}

// === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
const AGENTS = [
  { id: "general", label: "General", hint: "Ask me anything — I can search the web or check the time." },
  { id: "stock",   label: "Stock Analysis", hint: 'Try: "Analyse AAPL" · "Forecast TSLA for 6 months" · "Compare AAPL and MSFT"' },
];
// === END STOCK AGENT ROUTING ===

const NAV_ITEMS: { view: View; label: string; icon: React.ReactNode }[] = [
  {
    view: "chat",
    label: "Chat",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  {
    view: "docs",
    label: "Docs",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
      </svg>
    ),
  },
  {
    view: "dashboard",
    label: "Dashboard",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M9 21V9" />
      </svg>
    ),
  },
];

export default function ChatPage() {
  const [view, setView] = useState<View>("chat");
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [iframeLoading, setIframeLoading] = useState(false);
  const [iframeError, setIframeError] = useState(false);
  const [histories, setHistories] = useState<Record<string, Message[]>>({
    general: [],
    stock: [],
  });
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [statusLine, setStatusLine] = useState<string>("");
  // === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
  const [agentId, setAgentId] = useState("general");
  // === END STOCK AGENT ROUTING ===
  const [menuOpen, setMenuOpen] = useState(false);

  // Load persisted histories from localStorage on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("chat_histories");
      if (saved) {
        const parsed = JSON.parse(saved) as Record<string, { role: "user" | "assistant"; content: string; timestamp: string }[]>;
        const revived: Record<string, Message[]> = {};
        for (const [id, msgs] of Object.entries(parsed)) {
          revived[id] = msgs.map((m) => ({ ...m, timestamp: new Date(m.timestamp) }));
        }
        setHistories(revived);
      }
    } catch { /* ignore corrupt data */ }
  }, []);

  // Save histories to localStorage whenever they change
  useEffect(() => {
    try {
      localStorage.setItem("chat_histories", JSON.stringify(histories));
    } catch { /* ignore quota errors */ }
  }, [histories]);

  // Derived messages for the active agent
  const messages = histories[agentId] ?? [];

  // Scoped setter — always updates only the current agent's history
  const setMessages = (updater: Message[] | ((prev: Message[]) => Message[])) => {
    setHistories((h) => ({
      ...h,
      [agentId]: typeof updater === "function" ? updater(h[agentId] ?? []) : updater,
    }));
  };

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Close menu when clicking outside
  useEffect(() => {
    if (!menuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [menuOpen]);

  const switchView = (v: View) => {
    setView(v);
    setIframeUrl(null);
    setMenuOpen(false);
    if (v !== "chat") {
      setIframeLoading(true);
      setIframeError(false);
    }
  };

  const handleInternalLink = (href: string) => {
    const dashboardBase = process.env.NEXT_PUBLIC_DASHBOARD_URL ?? "http://127.0.0.1:8050";
    const docsBase = process.env.NEXT_PUBLIC_DOCS_URL ?? "http://127.0.0.1:8000";
    if (href.startsWith(dashboardBase)) {
      setView("dashboard");
      setIframeUrl(href);
      setIframeLoading(true);
      setIframeError(false);
    } else if (href.startsWith(docsBase)) {
      setView("docs");
      setIframeUrl(href);
      setIframeLoading(true);
      setIframeError(false);
    }
  };

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
    setStatusLine("Thinking...");

    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    try {
      const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://127.0.0.1:8181";
      const res = await fetch(`${backendUrl}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userMessage.content,
          history: messages.map((m) => ({ role: m.role, content: m.content })),
          agent_id: agentId,
        }),
      });

      if (!res.ok) {
        const errorContent =
          res.status === 504
            ? "Request timed out, please try again."
            : "Error connecting to server. Is the backend running?";
        setMessages([
          ...updatedMessages,
          { role: "assistant", content: errorContent, timestamp: new Date() },
        ]);
        return;
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const event = JSON.parse(trimmed) as Record<string, unknown>;
            if (event.type === "thinking") {
              const iter = event.iteration as number;
              setStatusLine(iter > 1 ? `Thinking... (step ${iter})` : "Thinking...");
            } else if (event.type === "tool_start") {
              setStatusLine(`${toolLabel(event.tool as string)}...`);
            } else if (event.type === "tool_done") {
              setStatusLine(`Got result from ${event.tool as string}...`);
            } else if (event.type === "warning") {
              setStatusLine("Max iterations reached, finalising...");
            } else if (event.type === "final") {
              setMessages([
                ...updatedMessages,
                {
                  role: "assistant",
                  content: event.response as string,
                  timestamp: new Date(),
                },
              ]);
              setStatusLine("");
            } else if (event.type === "error" || event.type === "timeout") {
              setMessages([
                ...updatedMessages,
                {
                  role: "assistant",
                  content: `Error: ${event.message as string}`,
                  timestamp: new Date(),
                },
              ]);
              setStatusLine("");
            }
          } catch { /* skip invalid JSON lines */ }
        }
      }
    } catch {
      setMessages([
        ...updatedMessages,
        {
          role: "assistant",
          content: "Error connecting to server. Is the backend running?",
          timestamp: new Date(),
        },
      ]);
      setStatusLine("");
    } finally {
      setLoading(false);
      setStatusLine("");
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
  };

  const iframeSrc =
    iframeUrl ??
    (view === "docs"
      ? (process.env.NEXT_PUBLIC_DOCS_URL ?? "http://127.0.0.1:8000")
      : (process.env.NEXT_PUBLIC_DASHBOARD_URL ?? "http://127.0.0.1:8050"));

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans">

      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-gray-200 shadow-sm shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-sm font-bold select-none">
            ✦
          </div>
          <div>
            <h1 className="font-semibold text-gray-900 leading-tight">AI Agent</h1>
            <span className="text-xs text-indigo-600 font-medium">Claude Sonnet 4.6</span>
          </div>

          {/* Agent toggle (chat only) / View label (docs + dashboard) */}
          {view === "chat" ? (
            /* === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 === */
            <div className="flex items-center gap-1 ml-4 bg-gray-100 rounded-lg p-0.5">
              {AGENTS.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setAgentId(a.id)}
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
            /* === END STOCK AGENT ROUTING === */
          ) : (
            <span className="ml-4 text-sm font-medium text-gray-500">
              {view === "docs" ? "Documentation" : "Dashboard"}
            </span>
          )}
        </div>

        {/* Right side of header */}
        <div className="flex items-center gap-2">
          {/* Open in new tab — shown when viewing docs or dashboard */}
          {view !== "chat" && (
            <a
              href={iframeSrc}
              target="_blank"
              rel="noopener noreferrer"
              title="Open in new tab"
              className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-indigo-600 transition-colors px-3 py-1.5 rounded-lg hover:bg-indigo-50"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                <polyline points="15 3 21 3 21 9" />
                <line x1="10" y1="14" x2="21" y2="3" />
              </svg>
              Open in new tab
            </a>
          )}

          {/* Clear chat — shown on chat view when there are messages */}
          {view === "chat" && messages.length > 0 && (
            <button
              onClick={() => setMessages([])}
              title="Clear chat"
              className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-red-500 transition-colors px-3 py-1.5 rounded-lg hover:bg-red-50"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                <path d="M10 11v6M14 11v6" />
                <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
              </svg>
              Clear
            </button>
          )}
        </div>
      </header>

      {/* Main content — chat UI or embedded iframe */}
      {view === "chat" ? (
        <>
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
                    {msg.role === "user" ? msg.content : <MarkdownContent content={msg.content} onInternalLink={handleInternalLink} />}
                  </div>
                  <span className="text-[11px] text-gray-400 px-1">
                    {formatTime(msg.timestamp)}
                  </span>
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex items-end gap-2.5">
                <div className="w-8 h-8 shrink-0 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-xs font-bold">
                  ✦
                </div>
                <div className="bg-white border border-gray-100 rounded-2xl rounded-bl-sm shadow-sm">
                  <StatusBadge text={statusLine || "Thinking..."} />
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </main>

          <footer className="bg-white border-t border-gray-200 px-4 py-3 shrink-0">
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
        </>
      ) : (
        /* Iframe wrapper with loading spinner and error fallback */
        <div className="flex-1 relative overflow-hidden">
          {iframeLoading && !iframeError && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
              <div className="flex flex-col items-center gap-3">
                <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                <span className="text-sm text-gray-500">Loading...</span>
              </div>
            </div>
          )}
          {iframeError && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
              <div className="flex flex-col items-center gap-3 text-center px-6">
                <p className="text-gray-600 font-medium">Could not load content.</p>
                <p className="text-sm text-gray-400">Make sure the service is running.</p>
                <a
                  href={iframeSrc}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-indigo-600 underline text-sm hover:text-indigo-800"
                >
                  Open in new tab ↗
                </a>
              </div>
            </div>
          )}
          <iframe
            src={iframeSrc}
            className="w-full h-full border-0"
            title={view === "docs" ? "Documentation" : "Dashboard"}
            onLoad={() => setIframeLoading(false)}
            onError={() => { setIframeLoading(false); setIframeError(true); }}
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals"
            referrerPolicy="no-referrer"
          />
        </div>
      )}

      {/* Bottom-right navigation menu */}
      <div className="fixed bottom-6 right-6 z-50" ref={menuRef}>
        <button
          onClick={() => setMenuOpen((v) => !v)}
          title="Open navigation"
          className="w-11 h-11 rounded-xl bg-white border border-gray-200 shadow-md flex items-center justify-center text-gray-500 hover:text-indigo-600 hover:border-indigo-300 hover:shadow-lg transition-all"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" />
            <rect x="14" y="3" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" />
          </svg>
        </button>

        {menuOpen && (
          <div className="absolute bottom-14 right-0 bg-white rounded-xl shadow-lg border border-gray-200 overflow-hidden min-w-[160px]">
            {NAV_ITEMS.map((item, idx) => (
              <div key={item.view}>
                {idx > 0 && <div className="border-t border-gray-100" />}
                <button
                  onClick={() => switchView(item.view)}
                  className={`w-full flex items-center gap-2.5 px-4 py-3 text-sm transition-colors text-left ${
                    view === item.view
                      ? "bg-indigo-50 text-indigo-600 font-medium"
                      : "text-gray-700 hover:bg-gray-50 hover:text-indigo-600"
                  }`}
                >
                  {item.icon}
                  {item.label}
                  {view === item.view && (
                    <span className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-500" />
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  );
}
