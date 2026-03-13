/**
 * Top header bar for the chat page.
 *
 * Shows the app logo, active agent toggle (chat mode) or current view label
 * (docs/dashboard/admin mode), a clear-chat button, and a profile chip that
 * opens a dropdown with Edit Profile, Change Password, and Sign Out.
 */

import { useState, useRef, useEffect, useMemo } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { clearTokens } from "@/lib/auth";
import { AGENTS, type View } from "@/lib/constants";
import type { UserProfile } from "@/hooks/useEditProfile";
import { BACKEND_URL } from "@/lib/config";

interface ChatHeaderProps {
  view: View;
  agentId: string;
  setAgentId: (id: string) => void;
  messages: { role: string }[];
  onClearMessages: () => void;
  profile: UserProfile | null;
  onEditProfile: () => void;
  onChangePassword: () => void;
  onManageSessions: () => void;
  onToggleMobileMenu?: () => void;
}

export function ChatHeader({
  view,
  agentId,
  setAgentId,
  messages,
  onClearMessages,
  profile,
  onEditProfile,
  onChangePassword,
  onManageSessions,
  onToggleMobileMenu,
}: ChatHeaderProps) {
  const router = useRouter();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [avatarErrSrc, setAvatarErrSrc] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [dropdownOpen]);

  const handleSignOut = () => {
    clearTokens();
    router.replace("/login");
  };

  // Build the avatar element: real image if available, else initials circle.
  const initials = profile?.full_name?.trim()
    ? profile.full_name.trim()[0].toUpperCase()
    : profile?.email?.[0]?.toUpperCase() ?? "?";

  const avatarSrc = useMemo(() => {
    if (!profile?.avatar_url) return null;
    return profile.avatar_url.startsWith("/")
      ? `${BACKEND_URL}${profile.avatar_url}`
      : profile.avatar_url;
  }, [profile?.avatar_url]);

  // Derive error state: only true if the errored src matches the current src.
  const avatarErr = avatarErrSrc !== null && avatarErrSrc === avatarSrc;

  const AvatarEl = avatarSrc && !avatarErr ? (
    <Image
      src={avatarSrc}
      alt=""
      width={32}
      height={32}
      onError={() => setAvatarErrSrc(avatarSrc)}
      className="w-8 h-8 rounded-full object-cover object-top border border-gray-200 dark:border-gray-600"
      unoptimized
    />
  ) : (
    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white text-sm font-semibold select-none">
      {initials}
    </div>
  );

  return (
    <header className="flex items-center justify-between px-3 md:px-6 py-2 md:py-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 shadow-sm shrink-0 transition-colors">
      {/* -- Left: hamburger (mobile) + logo + agent switcher -- */}
      <div className="flex items-center gap-2 md:gap-3 min-w-0">
        {/* Hamburger — mobile only */}
        {onToggleMobileMenu && (
          <button
            onClick={onToggleMobileMenu}
            className="w-11 h-11 flex items-center justify-center text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 rounded-lg md:hidden"
            aria-label="Open menu"
            data-testid="hamburger-menu"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
        )}

        <Image
          src="/images/aset-logo-final.svg"
          alt="ASET"
          width={48}
          height={48}
          className="h-9 md:h-12 w-auto drop-shadow-sm shrink-0"
          priority
        />

        {view === "chat" ? (
          <div className="flex items-center gap-1 ml-2 md:ml-4 bg-gray-100 dark:bg-gray-800 rounded-lg p-0.5" data-testid="agent-selector">
            {AGENTS.map((a) => (
              <button
                key={a.id}
                onClick={() => setAgentId(a.id)}
                className={`text-xs px-2 md:px-3 py-1.5 rounded-md font-medium transition-colors whitespace-nowrap ${
                  agentId === a.id
                    ? "bg-white dark:bg-gray-700 text-indigo-700 dark:text-indigo-400 shadow-sm"
                    : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                }`}
              >
                {a.label}
              </button>
            ))}
          </div>
        ) : (
          <span className="ml-2 md:ml-4 text-sm font-medium text-gray-500 dark:text-gray-400 truncate">
            {view === "docs" ? "Documentation" : view === "admin" ? "Admin" : "Dashboard"}
          </span>
        )}
      </div>

      {/* -- Right: clear button + profile chip -- */}
      <div className="flex items-center gap-2">
        {view === "chat" && messages.length > 0 && (
          <button
            onClick={onClearMessages}
            title="Clear chat"
            data-testid="clear-messages-button"
            className="flex items-center gap-1.5 text-sm text-gray-400 dark:text-gray-500 hover:text-red-500 dark:hover:text-red-400 transition-colors w-11 h-11 md:w-auto md:h-auto justify-center md:justify-start md:px-3 md:py-1.5 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              <path d="M10 11v6M14 11v6" />
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
            </svg>
            <span className="hidden md:inline">Clear</span>
          </button>
        )}

        {/* Profile chip + dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen((v) => !v)}
            className="flex items-center gap-2 px-2 py-1 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            title="Account"
            data-testid="profile-avatar"
          >
            {AvatarEl}
            {profile && (
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300 hidden sm:block max-w-[120px] truncate">
                {profile.full_name || profile.email}
              </span>
            )}
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>

          {dropdownOpen && (
            <div className="absolute right-0 top-full mt-2 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 overflow-hidden min-w-[200px] z-50">
              {/* Profile info */}
              {profile && (
                <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-700">
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">{profile.full_name}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{profile.email}</p>
                </div>
              )}

              {/* Edit Profile */}
              <button
                onClick={() => { setDropdownOpen(false); onEditProfile(); }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors text-left"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
                Edit Profile
              </button>

              {/* Change Password */}
              <button
                onClick={() => { setDropdownOpen(false); onChangePassword(); }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors text-left"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                  <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
                Change Password
              </button>

              {/* Manage Sessions */}
              <button
                onClick={() => { setDropdownOpen(false); onManageSessions(); }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors text-left"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                  <line x1="8" y1="21" x2="16" y2="21" />
                  <line x1="12" y1="17" x2="12" y2="21" />
                </svg>
                Manage Sessions
              </button>

              <div className="border-t border-gray-100 dark:border-gray-700" />

              {/* Sign out */}
              <button
                onClick={handleSignOut}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors text-left"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                  <polyline points="16 17 21 12 16 7" />
                  <line x1="21" y1="12" x2="9" y2="12" />
                </svg>
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
