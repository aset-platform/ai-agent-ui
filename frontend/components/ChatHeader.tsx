/**
 * Top header bar for the chat page.
 *
 * Shows the app logo, active agent toggle (chat mode) or current view label
 * (docs/dashboard/admin mode), a clear-chat button, and a profile chip that
 * opens a dropdown with Edit Profile, Change Password, and Sign Out.
 */

import { useState, useRef, useEffect, useMemo } from "react";
import Image from "next/image";
import {
  clearTokens,
  getSubscriptionTierFromToken,
  getUsageRemainingFromToken,
} from "@/lib/auth";
import { apiFetch } from "@/lib/apiFetch";
import { useChatContext } from "@/providers/ChatProvider";
import { type View } from "@/lib/constants";
import type { UserProfile } from "@/hooks/useEditProfile";
import { API_URL, BACKEND_URL } from "@/lib/config";

/** Compact usage indicator in the header bar. */
function UsageBadge() {
  const tier = getSubscriptionTierFromToken();
  const remaining = getUsageRemainingFromToken();

  // Premium (unlimited) — show a small badge
  if (remaining === null) {
    return (
      <span className="hidden sm:inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 px-2 py-1 rounded-full font-medium capitalize">
        {tier}
      </span>
    );
  }

  // Determine color: green < 50%, yellow 50-80%, red > 80%
  const quotas: Record<string, number> = { free: 3, pro: 30 };
  const total = quotas[tier] ?? 3;
  const used = total - remaining;
  const pct = total > 0 ? (used / total) * 100 : 0;
  const color =
    pct < 50
      ? "text-emerald-600 bg-emerald-50"
      : pct < 80
        ? "text-amber-600 bg-amber-50"
        : "text-red-600 bg-red-50";

  return (
    <span
      className={`hidden sm:inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full font-medium ${color}`}
      title={`${used}/${total} analyses used this month`}
    >
      {used}/{total}
    </span>
  );
}

interface ChatHeaderProps {
  view: View;
  messages: { role: string }[];
  onClearMessages: () => void;
  profile: UserProfile | null;
  onEditProfile: () => void;
  onChangePassword: () => void;
}

export function ChatHeader({
  view,
  messages,
  onClearMessages,
  profile,
  onEditProfile,
  onChangePassword,
}: ChatHeaderProps) {
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

  const chatContext = useChatContext();
  const handleSignOut = async () => {
    await chatContext.flush();
    // Backend /auth/logout clears the access_token + refresh_token
    // HttpOnly cookies; without this call proxy.ts sees the
    // refresh_token cookie (legacy-session hotfix) and bounces
    // /login back to /dashboard, leaving the user "logged in".
    try {
      await apiFetch(`${API_URL}/auth/logout`, { method: "POST" });
    } catch {
      // best-effort — fall through to local cleanup either way
    }
    clearTokens();
    window.location.href = "/login";
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
      className="w-8 h-8 rounded-full object-cover object-top border border-gray-200"
      unoptimized
    />
  ) : (
    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white text-sm font-semibold select-none">
      {initials}
    </div>
  );

  return (
    <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-gray-200 shadow-sm shrink-0">
      {/* ── Left: logo + agent switcher ─────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <Image
          src="/images/aset-logo-final.svg"
          alt="ASET"
          width={48}
          height={48}
          className="h-12 w-auto drop-shadow-sm"
          priority
        />

        <span className="ml-4 text-sm font-medium text-gray-500">
          {view === "docs"
            ? "Documentation"
            : view === "admin"
              ? "Admin"
              : "Dashboard"}
        </span>
      </div>

      {/* ── Right: clear button + profile chip ──────────────────────────── */}
      <div className="flex items-center gap-2">
        {(view as string) === "chat" && messages.length > 0 && (
          <button
            onClick={onClearMessages}
            title="Clear chat"
            data-testid="clear-messages-button"
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

        {/* Usage meter */}
        <UsageBadge />

        {/* Profile chip + dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen((v) => !v)}
            className="flex items-center gap-2 px-2 py-1 rounded-xl hover:bg-gray-100 transition-colors"
            title="Account"
            data-testid="profile-avatar"
          >
            {AvatarEl}
            {profile && (
              <span className="text-sm font-medium text-gray-700 hidden sm:block max-w-[120px] truncate">
                {profile.full_name || profile.email}
              </span>
            )}
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>

          {dropdownOpen && (
            <div className="absolute right-0 top-full mt-2 bg-white rounded-xl shadow-lg border border-gray-200 overflow-hidden min-w-[200px] z-50">
              {/* Profile info */}
              {profile && (
                <div className="px-4 py-3 border-b border-gray-100">
                  <p className="text-sm font-semibold text-gray-900 truncate">{profile.full_name}</p>
                  <p className="text-xs text-gray-500 truncate">{profile.email}</p>
                </div>
              )}

              {/* Edit Profile */}
              <button
                onClick={() => { setDropdownOpen(false); onEditProfile(); }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 hover:text-indigo-600 transition-colors text-left"
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
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 hover:text-indigo-600 transition-colors text-left"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                  <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
                Change Password
              </button>

              <div className="border-t border-gray-100" />

              {/* Sign out */}
              <button
                onClick={handleSignOut}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-red-500 hover:bg-red-50 transition-colors text-left"
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
