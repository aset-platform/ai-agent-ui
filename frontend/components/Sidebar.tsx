"use client";
/**
 * Collapsible sidebar navigation for the dashboard layout.
 *
 * Replaces the old floating-grid NavigationMenu with a persistent
 * sidebar that supports expanded (220 px) and collapsed (62 px)
 * states.  On mobile (< md) it renders as a fixed slide-out drawer
 * with a backdrop overlay.
 *
 * Auto-collapses when the chat panel is open and restores the
 * previous state when it closes.
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { NAV_ITEMS, type NavItem } from "@/lib/constants";
import { useChatContext } from "@/providers/ChatProvider";
import { useLayoutContext } from "@/providers/LayoutProvider";
import { useTheme } from "@/hooks/useTheme";
import type { UserProfile } from "@/hooks/useEditProfile";

// ----------------------------------------------------------------
// Props
// ----------------------------------------------------------------

interface SidebarProps {
  profile: UserProfile | null;
}

// ----------------------------------------------------------------
// Permission helper
// ----------------------------------------------------------------

function canSeeItem(
  item: NavItem,
  profile: UserProfile | null,
): boolean {
  if (item.superuserOnly) {
    if (!profile) return false;
    if (profile.role === "superuser") return true;
    return profile.page_permissions?.admin === true;
  }
  if (item.requiresInsights) {
    if (!profile) return false;
    if (profile.role === "superuser") return true;
    return (
      profile.page_permissions?.insights === true
    );
  }
  return true;
}

// ----------------------------------------------------------------
// Icon components
// ----------------------------------------------------------------

/** Sun icon for light-mode indicator. */
function SunIcon() {
  return (
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
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line
        x1="4.22" y1="4.22"
        x2="5.64" y2="5.64"
      />
      <line
        x1="18.36" y1="18.36"
        x2="19.78" y2="19.78"
      />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line
        x1="4.22" y1="19.78"
        x2="5.64" y2="18.36"
      />
      <line
        x1="18.36" y1="5.64"
        x2="19.78" y2="4.22"
      />
    </svg>
  );
}

/** Moon icon for dark-mode indicator. */
function MoonIcon() {
  return (
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
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

/** Chevron used for the collapse/expand toggle. */
function ChevronIcon({
  collapsed,
}: {
  collapsed: boolean;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      className={[
        "w-4 h-4 transition-transform duration-300",
        collapsed ? "rotate-180" : "",
      ].join(" ")}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

/** Chevron-down used for collapsible nav groups. */
function GroupChevron({
  open,
}: {
  open: boolean;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      className={[
        "w-3 h-3 transition-transform duration-200",
        open ? "rotate-0" : "-rotate-90",
      ].join(" ")}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

/** Close (X) icon for the mobile drawer header. */
function CloseIcon() {
  return (
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
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

// ----------------------------------------------------------------
// Sidebar component
// ----------------------------------------------------------------

export function Sidebar({ profile }: SidebarProps) {
  const pathname = usePathname();
  const chatContext = useChatContext();
  const layoutContext = useLayoutContext();
  const { resolvedTheme, toggle: toggleTheme } =
    useTheme();

  const {
    sidebarCollapsed,
    setSidebarCollapsed,
    toggleSidebar,
    mobileMenuOpen,
    setMobileMenuOpen,
  } = layoutContext;

  // Track the user's manual collapsed preference so we can
  // restore it after the chat panel closes.
  const preChatCollapsed = useRef(sidebarCollapsed);

  // Auto-collapse when chat opens; restore when it closes.
  useEffect(() => {
    if (chatContext.isOpen) {
      preChatCollapsed.current = sidebarCollapsed;
      setSidebarCollapsed(true);
    } else {
      setSidebarCollapsed(preChatCollapsed.current);
    }
    // Only react to chat open/close changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatContext.isOpen]);

  // Lock body scroll when mobile drawer is open.
  useEffect(() => {
    if (mobileMenuOpen) {
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = "";
      };
    }
  }, [mobileMenuOpen]);

  const [dashboardOpen, setDashboardOpen] =
    useState(true);

  const visibleItems = useMemo(
    () =>
      NAV_ITEMS.filter((item) =>
        canSeeItem(item, profile),
      ).map((item) =>
        item.children
          ? {
              ...item,
              children: item.children.filter((c) =>
                canSeeItem(c, profile),
              ),
            }
          : item,
      ),
    [profile],
  );

  const collapsed = sidebarCollapsed;

  // ---- Shared nav item renderer ----

  function renderNavItem(
    item: NavItem,
    mobile: boolean,
  ) {
    const isActive = pathname === item.href;

    const linkClasses = [
      "flex items-center gap-3 text-sm",
      "rounded-lg transition-colors",
      mobile ? "px-4 py-3" : "px-3 py-2.5",
      isActive
        ? [
            "bg-indigo-50 dark:bg-indigo-900/30",
            "text-indigo-600 dark:text-indigo-400",
            "font-medium",
            "border-l-2 border-indigo-500",
          ].join(" ")
        : [
            "text-gray-600 dark:text-gray-400",
            "hover:bg-gray-100",
            "dark:hover:bg-gray-800",
          ].join(" "),
    ].join(" ");

    return (
      <Link
        key={item.view}
        href={item.href}
        title={
          !mobile && collapsed ? item.label : undefined
        }
        data-testid={`sidebar-item-${item.view}`}
        className={linkClasses}
        onClick={() => {
          if (mobile) setMobileMenuOpen(false);
        }}
      >
        <span className="shrink-0">{item.icon}</span>
        {(mobile || !collapsed) && (
          <span className="truncate">
            {item.label}
          </span>
        )}
      </Link>
    );
  }

  // ---- Collapsible nav group renderer ----

  function renderNavGroup(
    item: NavItem,
    mobile: boolean,
  ) {
    const children = item.children ?? [];
    const isAnyChildActive = children.some(
      (c) => pathname === c.href,
    );
    const isParentExact = pathname === item.href;
    const isActive =
      isAnyChildActive || isParentExact;

    // In collapsed desktop mode, render as a
    // simple link (no expand/collapse).
    if (!mobile && collapsed) {
      return renderNavItem(item, false);
    }

    const parentClasses = [
      "flex items-center gap-3 text-sm w-full",
      "rounded-lg transition-colors cursor-pointer",
      mobile ? "px-4 py-3" : "px-3 py-2.5",
      isActive
        ? [
            "bg-indigo-50 dark:bg-indigo-900/30",
            "text-indigo-600 dark:text-indigo-400",
            "font-medium",
          ].join(" ")
        : [
            "text-gray-600 dark:text-gray-400",
            "hover:bg-gray-100",
            "dark:hover:bg-gray-800",
          ].join(" "),
    ].join(" ");

    return (
      <div key={item.view + "-group"}>
        <button
          type="button"
          className={parentClasses}
          onClick={() =>
            setDashboardOpen((prev) => !prev)
          }
          data-testid={`sidebar-group-${item.view}`}
        >
          <span className="shrink-0">
            {item.icon}
          </span>
          <span className="truncate flex-1 text-left">
            {item.label}
          </span>
          <GroupChevron open={dashboardOpen} />
        </button>

        <div
          className={[
            "overflow-hidden transition-all",
            "duration-200 ease-in-out",
            dashboardOpen
              ? "max-h-60 opacity-100"
              : "max-h-0 opacity-0",
          ].join(" ")}
        >
          <div className="pl-4 space-y-0.5 mt-0.5">
            {children.map((child) => {
              const childActive =
                pathname === child.href;
              const childClasses = [
                "flex items-center gap-3 text-sm",
                "rounded-lg transition-colors",
                mobile ? "px-4 py-2" : "px-3 py-2",
                childActive
                  ? [
                      "bg-indigo-50",
                      "dark:bg-indigo-900/30",
                      "text-indigo-600",
                      "dark:text-indigo-400",
                      "font-medium",
                      "border-l-2",
                      "border-indigo-500",
                    ].join(" ")
                  : [
                      "text-gray-500",
                      "dark:text-gray-500",
                      "hover:bg-gray-100",
                      "dark:hover:bg-gray-800",
                      "hover:text-gray-700",
                      "dark:hover:text-gray-300",
                    ].join(" "),
              ].join(" ");

              return (
                <Link
                  key={child.href}
                  href={child.href}
                  className={childClasses}
                  data-testid={`sidebar-child-${child.label.toLowerCase().replace(/\s+/g, "-")}`}
                  onClick={() => {
                    if (mobile)
                      setMobileMenuOpen(false);
                  }}
                >
                  <span className="shrink-0">
                    {child.icon}
                  </span>
                  <span className="truncate">
                    {child.label}
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // ---- Theme toggle button ----

  function renderThemeToggle(mobile: boolean) {
    return (
      <button
        onClick={toggleTheme}
        data-testid="sidebar-theme-toggle"
        title={
          !mobile && collapsed
            ? resolvedTheme === "dark"
              ? "Light Mode"
              : "Dark Mode"
            : undefined
        }
        className={[
          "flex items-center gap-3 text-sm w-full",
          "rounded-lg transition-colors",
          mobile ? "px-4 py-3" : "px-3 py-2.5",
          "text-gray-600 dark:text-gray-400",
          "hover:bg-gray-100 dark:hover:bg-gray-800",
        ].join(" ")}
      >
        {resolvedTheme === "dark" ? (
          <SunIcon />
        ) : (
          <MoonIcon />
        )}
        {(mobile || !collapsed) && (
          <span className="truncate">
            {resolvedTheme === "dark"
              ? "Light Mode"
              : "Dark Mode"}
          </span>
        )}
      </button>
    );
  }

  // ---- Logo section ----

  function renderLogo(mobile: boolean) {
    const showFull = mobile || !collapsed;
    return (
      <div
        className={[
          "flex items-center",
          "border-b border-gray-200",
          "dark:border-gray-700",
          showFull ? "px-4 py-4 gap-3" : "px-3 py-4",
          !showFull ? "justify-center" : "",
        ].join(" ")}
      >
        <Image
          src="/images/aset-logo-final.svg"
          alt="ASET"
          width={showFull ? 28 : 24}
          height={showFull ? 28 : 24}
          className="shrink-0"
          priority
        />
        {showFull && (
          <span
            className={[
              "text-sm font-semibold tracking-tight",
              "text-gray-900 dark:text-gray-100",
              "truncate",
            ].join(" ")}
          >
            ASET Platform
          </span>
        )}
      </div>
    );
  }

  // ================================================================
  // Render
  // ================================================================

  return (
    <>
      {/* -- Desktop sidebar (md+) ----------------------------- */}
      <aside
        data-testid="sidebar"
        style={{
          width: collapsed ? 62 : 220,
        }}
        className={[
          "hidden md:flex flex-col",
          "fixed inset-y-0 left-0 z-30",
          "bg-white dark:bg-gray-900",
          "border-r border-gray-200",
          "dark:border-gray-700",
          "transition-all duration-300 ease-in-out",
        ].join(" ")}
      >
        {/* Logo */}
        {renderLogo(false)}

        {/* Nav items */}
        <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
          {visibleItems.map((item) =>
            item.children
              ? renderNavGroup(item, false)
              : renderNavItem(item, false),
          )}
        </nav>

        {/* Footer: theme + collapse toggle */}
        <div
          className={[
            "border-t border-gray-200",
            "dark:border-gray-700",
            "px-2 py-2 space-y-1",
          ].join(" ")}
        >
          {renderThemeToggle(false)}

          <button
            onClick={toggleSidebar}
            data-testid="sidebar-collapse-toggle"
            title={
              collapsed
                ? "Expand sidebar"
                : "Collapse sidebar"
            }
            className={[
              "flex items-center gap-3 text-sm",
              "w-full rounded-lg transition-colors",
              "px-3 py-2.5",
              "text-gray-600 dark:text-gray-400",
              "hover:bg-gray-100",
              "dark:hover:bg-gray-800",
            ].join(" ")}
          >
            <ChevronIcon collapsed={collapsed} />
            {!collapsed && (
              <span className="truncate">
                Collapse
              </span>
            )}
          </button>
        </div>
      </aside>

      {/* -- Mobile drawer (< md) ------------------------------ */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 z-50 md:hidden"
          data-testid="sidebar-mobile-drawer"
        >
          {/* Backdrop */}
          <div
            className={[
              "absolute inset-0",
              "bg-black/40 transition-opacity",
            ].join(" ")}
            onClick={() => setMobileMenuOpen(false)}
          />

          {/* Drawer panel */}
          <nav
            className={[
              "absolute inset-y-0 left-0 w-64",
              "bg-white dark:bg-gray-900",
              "shadow-xl flex flex-col",
              "animate-slide-in-left",
              "transition-colors",
            ].join(" ")}
          >
            {/* Header */}
            <div
              className={[
                "flex items-center justify-between",
                "px-4 py-4",
                "border-b border-gray-200",
                "dark:border-gray-700",
              ].join(" ")}
            >
              <div className="flex items-center gap-3">
                <Image
                  src="/images/aset-logo-final.svg"
                  alt="ASET"
                  width={28}
                  height={28}
                  priority
                />
                <span
                  className={[
                    "text-sm font-semibold",
                    "text-gray-900",
                    "dark:text-gray-100",
                  ].join(" ")}
                >
                  ASET Platform
                </span>
              </div>
              <button
                onClick={() =>
                  setMobileMenuOpen(false)
                }
                className={[
                  "w-10 h-10 flex items-center",
                  "justify-center rounded-lg",
                  "text-gray-400 dark:text-gray-500",
                  "hover:text-gray-600",
                  "dark:hover:text-gray-300",
                ].join(" ")}
                aria-label="Close menu"
              >
                <CloseIcon />
              </button>
            </div>

            {/* Nav items */}
            <div className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
              {visibleItems.map((item) =>
                item.children
                  ? renderNavGroup(item, true)
                  : renderNavItem(item, true),
              )}
            </div>

            {/* Footer: theme toggle */}
            <div
              className={[
                "border-t border-gray-200",
                "dark:border-gray-700",
                "px-2 py-2",
              ].join(" ")}
            >
              {renderThemeToggle(true)}
            </div>
          </nav>
        </div>
      )}
    </>
  );
}
