"use client";
/**
 * Authenticated layout shell — wraps all protected routes with
 * context providers, auth guard, and the app chrome: sidebar,
 * header, chat panel, and FAB.
 */

import { useState, useEffect } from "react";
import { useAuthGuard } from "@/hooks/useAuthGuard";
import {
  ChatProvider,
  useChatContext,
} from "@/providers/ChatProvider";
import {
  useLayoutContext,
} from "@/providers/LayoutProvider";
import { LayoutProvider } from "@/providers/LayoutProvider";
import { PortfolioActionsProvider } from "@/providers/PortfolioActionsProvider";
import { useEditProfile, type UserProfile } from "@/hooks/useEditProfile";
import { useChangePassword } from "@/hooks/useChangePassword";
import { useSessionManagement } from "@/hooks/useSessionManagement";
import { Sidebar } from "@/components/Sidebar";
import { AppHeader } from "@/components/AppHeader";
import { ChatPanel } from "@/components/ChatPanel";
import { EditProfileModal } from "@/components/EditProfileModal";
import { ChangePasswordModal } from "@/components/ChangePasswordModal";
import { SessionManagementModal } from "@/components/SessionManagementModal";
import { UpgradeBanner } from "@/components/UpgradeBanner";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import { getSessionIdFromToken } from "@/lib/auth";

function AuthenticatedShell({
  children,
}: {
  children: React.ReactNode;
}) {
  const [profile, setProfile] =
    useState<UserProfile | null>(null);
  const [profileInitialTab, setProfileInitialTab] =
    useState<"profile" | "billing" | "audit">("profile");
  const { sidebarCollapsed } = useLayoutContext();
  const { isOpen: chatOpen } = useChatContext();
  const editProfile = useEditProfile();
  const changePassword = useChangePassword();
  const sessionMgmt = useSessionManagement();

  // Sidebar width: 220px expanded, 62px collapsed,
  // 0 on mobile (sidebar is hidden below md).
  const sidebarW =
    chatOpen || sidebarCollapsed ? 62 : 220;

  // Fetch profile on mount
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API_URL}/auth/me`, {
      signal: controller.signal,
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: UserProfile | null) => {
        if (data) setProfile(data);
      })
      .catch((err: unknown) => {
        if (
          err instanceof Error &&
          err.name === "AbortError"
        )
          return;
      });
    return () => controller.abort();
  }, []);

  const handleEditProfileSave = async (
    fullName: string,
    avatarFile: File | null,
  ) => {
    const updated = await editProfile.save(
      fullName,
      avatarFile,
    );
    if (updated) setProfile(updated);
  };

  const handleChangePasswordSave = async (
    newPw: string,
    confirmPw: string,
  ) => {
    await changePassword.save(
      profile?.email ?? "",
      newPw,
      confirmPw,
    );
  };

  return (
    <div className="h-screen bg-gray-50 dark:bg-gray-950 font-sans transition-colors overflow-hidden">
      <Sidebar profile={profile} />

      <div
        className="flex flex-col h-full min-w-0 transition-all duration-300 md:ml-[var(--sidebar-w)]"
        style={{
          "--sidebar-w": `${sidebarW}px`,
        } as React.CSSProperties}
      >
        <AppHeader
          profile={profile}
          onEditProfile={() => {
            setProfileInitialTab("profile");
            editProfile.open();
          }}
          onChangePassword={changePassword.open}
          onManageSessions={sessionMgmt.open}
          onBilling={() => {
            setProfileInitialTab("billing");
            editProfile.open();
          }}
          onActivityLog={() => {
            setProfileInitialTab("audit");
            editProfile.open();
          }}
        />
        <UpgradeBanner
          onUpgrade={() => {
            setProfileInitialTab("billing");
            editProfile.open();
          }}
        />
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>

      <ChatPanel />

      <EditProfileModal
        isOpen={editProfile.isOpen}
        initialTab={profileInitialTab}
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

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  useAuthGuard();

  // Previously gated behind `mounted` state to avoid hydration
  // mismatches — pushed FCP + LCP to ~3.5 s because Lighthouse
  // couldn't measure any meaningful paint until hydration
  // finished. All providers here are SSR-safe (useState w/
  // stable defaults, localStorage/WebSocket reads deferred to
  // useEffect). Removing the gate lets server-rendered page
  // content (see dashboard/page.tsx) paint in the first HTML
  // response, enabling LCP < 2 s on hero-driven routes.
  return (
    <LayoutProvider>
      <ChatProvider>
        <PortfolioActionsProvider>
          <AuthenticatedShell>
            {children}
          </AuthenticatedShell>
        </PortfolioActionsProvider>
      </ChatProvider>
    </LayoutProvider>
  );
}
