"use client";
/**
 * Simplified top header bar for the dashboard layout.
 *
 * Shows a hamburger menu (mobile), the ASET logo (mobile), a page title
 * derived from the current route, a chat-panel toggle (mobile), and a
 * profile chip with dropdown for account actions and sign-out.
 */

import { useState, useRef, useEffect, useMemo } from "react";
import Image from "next/image";
import { useRouter, usePathname } from "next/navigation";
import { clearTokens } from "@/lib/auth";
import { useChatContext } from "@/providers/ChatProvider";
import { useLayoutContext } from "@/providers/LayoutProvider";
import type { UserProfile } from "@/hooks/useEditProfile";
import { BACKEND_URL } from "@/lib/config";

interface AppHeaderProps {
  profile: UserProfile | null;
  onEditProfile: () => void;
  onChangePassword: () => void;
  onManageSessions: () => void;
  onActivityLog?: () => void;
}

export function AppHeader({
  profile,
  onEditProfile,
  onChangePassword,
  onManageSessions,
  onActivityLog,
}: AppHeaderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const chatContext = useChatContext();
  const layoutContext = useLayoutContext();

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [avatarErrSrc, setAvatarErrSrc] = useState<string | null>(
    null,
  );
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () =>
      document.removeEventListener("mousedown", handleClick);
  }, [dropdownOpen]);

  // Derive breadcrumb title from pathname
  const pageTitle = useMemo(() => {
    const segments = pathname.split("/").filter(Boolean);
    const root = segments[0] ?? "";
    const sub = segments[1] ?? "";

    const rootTitles: Record<string, string> = {
      dashboard: "Portfolio",
      analytics: "Dashboard",
      docs: "Docs",
      admin: "Admin",
    };

    const subTitles: Record<string, string> = {
      analysis: "Analysis",
      insights: "Insights",
      marketplace: "Link Stock",
    };

    const rootTitle = rootTitles[root] ?? "Home";
    if (sub && subTitles[sub]) {
      return `${rootTitle} → ${subTitles[sub]}`;
    }
    if (root === "analytics" && !sub) {
      return "Dashboard → Home";
    }
    return rootTitle;
  }, [pathname]);

  const handleSignOut = async () => {
    await chatContext.flush();
    clearTokens();
    router.replace("/login");
  };

  // Avatar: real image if available, else initials circle
  const initials = profile?.full_name?.trim()
    ? profile.full_name.trim()[0].toUpperCase()
    : profile?.email?.[0]?.toUpperCase() ?? "?";

  const avatarSrc = useMemo(() => {
    if (!profile?.avatar_url) return null;
    return profile.avatar_url.startsWith("/")
      ? `${BACKEND_URL}${profile.avatar_url}`
      : profile.avatar_url;
  }, [profile?.avatar_url]);

  const avatarErr =
    avatarErrSrc !== null && avatarErrSrc === avatarSrc;

  const AvatarEl =
    avatarSrc && !avatarErr ? (
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
    <header className="h-14 flex items-center justify-between px-4 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 shadow-sm shrink-0 transition-colors">
      {/* -- Left: hamburger (mobile) + logo (mobile) + title -- */}
      <div className="flex items-center gap-2 min-w-0">
        {/* Hamburger — mobile only */}
        <button
          onClick={() =>
            layoutContext.setMobileMenuOpen((v) => !v)
          }
          className="w-10 h-10 flex items-center justify-center text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 rounded-lg md:hidden"
          aria-label="Toggle navigation menu"
          data-testid="hamburger-menu"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="w-5 h-5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>

        {/* ASET logo — mobile only */}
        <Image
          src="/images/aset-logo-final.svg"
          alt="ASET"
          width={36}
          height={36}
          className="h-8 w-auto drop-shadow-sm shrink-0 md:hidden"
          priority
        />

        {/* Page title */}
        <h1 className="text-sm font-semibold text-gray-800 dark:text-gray-200 truncate ml-1 md:ml-0">
          {pageTitle}
        </h1>
      </div>

      {/* -- Right: chat toggle + profile chip -- */}
      <div className="flex items-center gap-2">
        {/* Chat panel toggle — all screen sizes */}
        <button
          onClick={() => chatContext.togglePanel()}
          className="w-10 h-10 flex items-center justify-center text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 rounded-lg transition-colors"
          aria-label="Toggle chat panel"
          data-testid="chat-toggle"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="w-5 h-5"
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
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>

          {dropdownOpen && (
            <div className="absolute right-0 top-full mt-2 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 overflow-hidden min-w-[200px] z-50">
              {/* Profile info */}
              {profile && (
                <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-700">
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
                    {profile.full_name}
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                    {profile.email}
                  </p>
                </div>
              )}

              {/* Edit Profile */}
              <button
                onClick={() => {
                  setDropdownOpen(false);
                  onEditProfile();
                }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors text-left"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="w-4 h-4 shrink-0"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
                Edit Profile
              </button>

              {/* Change Password */}
              <button
                onClick={() => {
                  setDropdownOpen(false);
                  onChangePassword();
                }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors text-left"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="w-4 h-4 shrink-0"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <rect
                    x="3"
                    y="11"
                    width="18"
                    height="11"
                    rx="2"
                    ry="2"
                  />
                  <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
                Change Password
              </button>

              {/* Manage Sessions */}
              <button
                onClick={() => {
                  setDropdownOpen(false);
                  onManageSessions();
                }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors text-left"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="w-4 h-4 shrink-0"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <rect
                    x="2"
                    y="3"
                    width="20"
                    height="14"
                    rx="2"
                    ry="2"
                  />
                  <line x1="8" y1="21" x2="16" y2="21" />
                  <line x1="12" y1="17" x2="12" y2="21" />
                </svg>
                Manage Sessions
              </button>

              {/* Activity Log */}
              <button
                onClick={() => {
                  setDropdownOpen(false);
                  onActivityLog?.();
                }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors text-left"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="w-4 h-4 shrink-0"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                Activity Log
              </button>

              <div className="border-t border-gray-100 dark:border-gray-700" />

              {/* Sign out */}
              <button
                onClick={handleSignOut}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors text-left"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="w-4 h-4 shrink-0"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
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
