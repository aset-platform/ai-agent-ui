"use client";
/**
 * Chat page — root SPA shell.
 *
 * Composes hooks and components to render the full chat interface, including
 * the agent switcher, message list, streaming input, and iframe views for
 * docs/dashboard/admin.
 */

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { getAccessToken } from "@/lib/auth";
import { apiFetch } from "@/lib/apiFetch";
import { AGENTS, type View } from "@/lib/constants";
import { useAuthGuard } from "@/hooks/useAuthGuard";
import { useChatHistory } from "@/hooks/useChatHistory";
import { useSendMessage } from "@/hooks/useSendMessage";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useEditProfile, type UserProfile } from "@/hooks/useEditProfile";
import { useChangePassword } from "@/hooks/useChangePassword";
import { useSessionManagement } from "@/hooks/useSessionManagement";
import { useTheme } from "@/hooks/useTheme";
import { StatusBadge } from "@/components/StatusBadge";
import { ChatHeader } from "@/components/ChatHeader";
import { ChatInput } from "@/components/ChatInput";
import { MessageBubble } from "@/components/MessageBubble";
import { IFrameView } from "@/components/IFrameView";
import { NavigationMenu } from "@/components/NavigationMenu";
import { EditProfileModal } from "@/components/EditProfileModal";
import { ChangePasswordModal } from "@/components/ChangePasswordModal";
import { SessionManagementModal } from "@/components/SessionManagementModal";
import { API_URL, DASHBOARD_URL, DOCS_URL } from "@/lib/config";
import { getSessionIdFromToken } from "@/lib/auth";

export default function ChatPage() {
  useAuthGuard();

  const [view, setView] = useState<View>("chat");
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [iframeLoading, setIframeLoading] = useState(false);
  const [iframeError, setIframeError] = useState(false);
  const [agentId, setAgentId] = useState("general");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [statusLine, setStatusLine] = useState<string>("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [profile, setProfile] = useState<UserProfile | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const { messages, setMessages } = useChatHistory(agentId);
  const ws = useWebSocket();

  const { sendMessage, handleKeyDown, handleInput } = useSendMessage({
    agentId,
    messages,
    setMessages,
    setLoading,
    setStatusLine,
    input,
    setInput,
    textareaRef,
    ws,
  });

  const theme = useTheme();
  const editProfile = useEditProfile();
  const changePassword = useChangePassword();
  const sessionMgmt = useSessionManagement();

  // Fetch user profile on mount — Fix #17: AbortController cancels if unmounted
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API_URL}/auth/me`, { signal: controller.signal })
      .then((res) => res.ok ? res.json() : null)
      .then((data: UserProfile | null) => { if (data) setProfile(data); })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
      });
    return () => controller.abort();
  }, []);

  // Scroll to bottom when messages or loading state changes
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Fix #10: stable handler reference — useCallback so identity is preserved
  // across effect re-runs when menuOpen changes.
  // Only fires on desktop — on mobile the drawer has its own close handlers.
  const handleMenuOutsideClick = useCallback((e: MouseEvent) => {
    if (
      menuRef.current &&
      menuRef.current.offsetParent !== null &&
      !menuRef.current.contains(e.target as Node)
    ) {
      setMenuOpen(false);
    }
  }, []);

  // Close menu when clicking outside
  useEffect(() => {
    if (!menuOpen) return;
    document.addEventListener("mousedown", handleMenuOutsideClick);
    return () => document.removeEventListener("mousedown", handleMenuOutsideClick);
  }, [menuOpen, handleMenuOutsideClick]);

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
    if (href.startsWith(DASHBOARD_URL)) {
      const token = getAccessToken();
      const sep = href.includes("?") ? "&" : "?";
      const params = token
        ? `token=${encodeURIComponent(token)}&theme=${theme.resolvedTheme}`
        : `theme=${theme.resolvedTheme}`;
      const dashUrl = `${href}${sep}${params}`;
      setView("dashboard");
      setIframeUrl(dashUrl);
      setIframeLoading(true);
      setIframeError(false);
    } else if (href.startsWith(DOCS_URL)) {
      setView("docs");
      setIframeUrl(href);
      setIframeLoading(true);
      setIframeError(false);
    }
  };

  // Fix #7: memoize iframeSrc — avoids calling getAccessToken() on every render
  const iframeSrc = useMemo(() => {
    if (view === "docs") return iframeUrl ?? DOCS_URL;
    const defaultUrl =
      view === "admin" ? `${DASHBOARD_URL}/admin/users` :
      view === "insights" ? `${DASHBOARD_URL}/insights` :
      DASHBOARD_URL;
    const base = iframeUrl ?? defaultUrl;
    const token = getAccessToken();
    if (!token) return base;
    const sep = base.includes("?") ? "&" : "?";
    const params = `token=${encodeURIComponent(token)}&theme=${theme.resolvedTheme}`;
    return `${base}${sep}${params}`;
  }, [view, iframeUrl, theme.resolvedTheme]);

  // Fix #8: memoize — avoids O(n) AGENTS.find() scan on every keystroke
  const agentHint = useMemo(
    () => AGENTS.find((a) => a.id === agentId)?.hint,
    [agentId]
  );

  const iframeTitle =
    view === "docs" ? "Documentation" :
    view === "admin" ? "Admin" :
    view === "insights" ? "Insights" :
    "Dashboard";

  const handleEditProfileSave = async (fullName: string, avatarFile: File | null) => {
    const updated = await editProfile.save(fullName, avatarFile);
    if (updated) setProfile(updated);
  };

  const handleChangePasswordSave = async (newPw: string, confirmPw: string) => {
    await changePassword.save(profile?.email ?? "", newPw, confirmPw);
  };

  return (
    <div className="flex flex-col h-screen bg-gray-50 dark:bg-gray-950 font-sans transition-colors">
      <ChatHeader
        view={view}
        agentId={agentId}
        setAgentId={setAgentId}
        messages={messages}
        onClearMessages={() => setMessages([])}
        profile={profile}
        onEditProfile={editProfile.open}
        onChangePassword={changePassword.open}
        onManageSessions={sessionMgmt.open}
        onToggleMobileMenu={() => setMenuOpen((v) => !v)}
      />

      {view === "chat" ? (
        <>
          <main className="flex-1 overflow-y-auto px-3 md:px-4 py-4 md:py-6 space-y-4 md:space-y-6 transition-colors">
            {messages.length === 0 && !loading && (
              <div className="flex flex-col items-center justify-center h-full text-center gap-4 pb-24">
                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-2xl shadow-lg">
                  ✦
                </div>
                <div>
                  <p className="text-gray-700 dark:text-gray-200 font-medium text-lg">How can I help you today?</p>
                  <p className="text-gray-400 dark:text-gray-500 text-sm mt-1">
                    {agentHint}
                  </p>
                </div>
              </div>
            )}

            {/* Fix #2: stable key — timestamp+role composite avoids full list re-render */}
            {messages.map((msg, i) => (
              <MessageBubble
                key={`${msg.timestamp.getTime()}-${msg.role}-${i}`}
                message={msg}
                onInternalLink={handleInternalLink}
              />
            ))}

            {loading && (
              <div className="flex items-end gap-2.5">
                <div className="w-8 h-8 shrink-0 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white text-xs font-bold">
                  ✦
                </div>
                <div className="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-2xl rounded-bl-sm shadow-sm">
                  <StatusBadge text={statusLine || "Thinking..."} />
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </main>

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
        <IFrameView
          src={iframeSrc}
          title={iframeTitle}
          loading={iframeLoading}
          error={iframeError}
          onLoad={() => setIframeLoading(false)}
          onError={() => { setIframeLoading(false); setIframeError(true); }}
        />
      )}

      <NavigationMenu
        menuOpen={menuOpen}
        setMenuOpen={setMenuOpen}
        menuRef={menuRef}
        currentView={view}
        onSwitchView={switchView}
        profile={profile}
        resolvedTheme={theme.resolvedTheme}
        onToggleTheme={theme.toggle}
      />

      <EditProfileModal
        isOpen={editProfile.isOpen}
        profile={profile}
        saving={editProfile.saving}
        error={editProfile.error}
        onClose={editProfile.close}
        onSave={handleEditProfileSave}
      />

      <ChangePasswordModal
        isOpen={changePassword.isOpen}
        saving={changePassword.saving}
        error={changePassword.error}
        onClose={changePassword.close}
        onSave={handleChangePasswordSave}
      />

      <SessionManagementModal
        isOpen={sessionMgmt.isOpen}
        sessions={sessionMgmt.sessions}
        loading={sessionMgmt.loading}
        revoking={sessionMgmt.revoking}
        revokingAll={sessionMgmt.revokingAll}
        error={sessionMgmt.error}
        currentSessionId={getSessionIdFromToken()}
        onClose={sessionMgmt.close}
        onRevoke={sessionMgmt.revokeSession}
        onRevokeAll={sessionMgmt.revokeAllSessions}
      />
    </div>
  );
}
