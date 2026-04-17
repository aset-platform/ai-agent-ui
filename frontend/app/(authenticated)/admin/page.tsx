"use client";
/**
 * Native Admin page — replaces the Dash iframe.
 *
 * Three tabs: Users, Audit Log, LLM Observability.
 * Role-gated: superuser or admin page permission.
 */

import {
  useState,
  useEffect,
  useMemo,
  useCallback,
  Suspense,
} from "react";
import {
  useSearchParams,
  useRouter,
} from "next/navigation";
import {
  useAdminUsers,
  useAdminAudit,
  useObservability,
  useAdminMaintenance,
} from "@/hooks/useAdminData";
import type {
  TriageEntry,
  RetentionResult,
  GapResult,
  UsageUser,
} from "@/hooks/useAdminData";
import type { UserFormData } from "@/components/admin/UserModal";
import { UserModal } from "@/components/admin/UserModal";
import { ResetPasswordModal } from "@/components/admin/ResetPasswordModal";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { SchedulerTab } from "@/components/admin/SchedulerTab";
import { DataHealthPanel } from "@/components/admin/DataHealthPanel";
import { BackupHealthPanel } from "@/components/admin/BackupHealthPanel";
import {
  InsightsTable,
  type Column,
} from "@/components/insights/InsightsTable";
import { WidgetSkeleton } from "@/components/widgets/WidgetSkeleton";
import { WidgetError } from "@/components/widgets/WidgetError";
import {
  downloadCsv,
  type CsvColumn,
} from "@/lib/downloadCsv";
import type {
  UserResponse,
  AuditEvent,
  CascadeEvent,
} from "@/lib/types";

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function fmtDate(v: string | null): string {
  if (!v) return "\u2014";
  return v.slice(0, 10);
}

function fmtTimestamp(v: string | null): string {
  if (!v) return "\u2014";
  return v.slice(0, 19).replace("T", " ");
}

function roleBadge(
  role: string,
): React.ReactNode {
  const cls =
    role === "superuser"
      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
      : "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400";
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}
    >
      {role}
    </span>
  );
}

function statusBadge(
  active: boolean,
): React.ReactNode {
  const cls = active
    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
    : "bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400";
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}
    >
      {active ? "Active" : "Inactive"}
    </span>
  );
}

function eventBadge(
  eventType: string,
): React.ReactNode {
  return (
    <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
      {eventType}
    </span>
  );
}

function parseMetadata(
  meta: string | Record<string, unknown>,
): string {
  if (!meta) return "\u2014";
  if (typeof meta === "string") {
    try {
      const parsed = JSON.parse(meta);
      return Object.entries(parsed)
        .map(([k, v]) => `${k}: ${v}`)
        .join(", ");
    } catch {
      return meta;
    }
  }
  return Object.entries(meta)
    .map(([k, v]) => `${k}: ${v}`)
    .join(", ");
}

function truncId(id: string | null): string {
  if (!id) return "\u2014";
  return id.length > 8
    ? `${id.slice(0, 8)}\u2026`
    : id;
}

// ---------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------

const auditCols: Column<AuditEvent>[] = [
  {
    key: "event_timestamp",
    label: "When",
    render: (r) => fmtTimestamp(r.event_timestamp),
  },
  {
    key: "event_type",
    label: "Event",
    render: (r) => eventBadge(r.event_type),
  },
  {
    key: "actor_user_id",
    label: "Actor",
    render: (r) => (
      <code className="text-xs">
        {truncId(r.actor_user_id)}
      </code>
    ),
  },
  {
    key: "target_user_id",
    label: "Target",
    render: (r) => (
      <code className="text-xs">
        {truncId(r.target_user_id)}
      </code>
    ),
  },
  {
    key: "metadata",
    label: "Details",
    sortable: false,
    render: (r) => (
      <span className="text-xs text-gray-500 dark:text-gray-400">
        {parseMetadata(r.metadata)}
      </span>
    ),
  },
];

// ---------------------------------------------------------------
// CSV column definitions
// ---------------------------------------------------------------

const userCsvCols: CsvColumn<UserResponse>[] = [
  { key: "full_name", header: "Name" },
  { key: "email", header: "Email" },
  { key: "role", header: "Role" },
  {
    key: "is_active",
    header: "Status",
    format: (v) => (v ? "Active" : "Inactive"),
  },
  {
    key: "created_at",
    header: "Created",
    format: (v) =>
      v ? String(v).slice(0, 10) : "",
  },
  {
    key: "last_login_at",
    header: "Last Login",
    format: (v) =>
      v ? String(v).slice(0, 10) : "",
  },
];

const auditCsvCols: CsvColumn<AuditEvent>[] = [
  {
    key: "event_timestamp",
    header: "Timestamp",
    format: (v) =>
      v
        ? String(v).slice(0, 19).replace("T", " ")
        : "",
  },
  { key: "event_type", header: "Event" },
  { key: "actor_user_id", header: "Actor" },
  { key: "target_user_id", header: "Target" },
  {
    key: "metadata",
    header: "Details",
    format: (v) => parseMetadata(v as string),
  },
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const txnCsvCols: CsvColumn<Record<string, any>>[] =
  [
    {
      key: "created_at",
      header: "Date",
      format: (v) =>
        v ? String(v).slice(0, 19) : "",
    },
    {
      key: "user_id",
      header: "User ID",
      format: (v) => String(v ?? ""),
    },
    { key: "user_name", header: "Name" },
    { key: "user_email", header: "Email" },
    { key: "gateway", header: "Gateway" },
    { key: "event_type", header: "Event" },
    {
      key: "amount",
      header: "Amount",
      format: (v, r) =>
        v ? `${r.currency} ${v}` : "",
    },
    { key: "tier_before", header: "Tier Before" },
    { key: "tier_after", header: "Tier After" },
    { key: "status", header: "Status" },
  ];

// ---------------------------------------------------------------
// Users Tab
// ---------------------------------------------------------------

function UsersTab() {
  const admin = useAdminUsers();
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<
    "add" | "edit"
  >("add");
  const [editUser, setEditUser] =
    useState<UserResponse | null>(null);
  const [modalSaving, setModalSaving] =
    useState(false);
  const [modalError, setModalError] = useState("");

  // Reset password modal state.
  const [resetOpen, setResetOpen] = useState(false);
  const [resetUser, setResetUser] =
    useState<UserResponse | null>(null);
  const [resetSaving, setResetSaving] =
    useState(false);
  const [resetError, setResetError] = useState("");
  const [deactivateUser, setDeactivateUser] =
    useState<UserResponse | null>(null);

  const filtered = useMemo(() => {
    if (!search.trim()) return admin.users;
    const q = search.toLowerCase();
    return admin.users.filter(
      (u) =>
        u.full_name.toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q) ||
        u.role.toLowerCase().includes(q),
    );
  }, [admin.users, search]);

  const openAdd = useCallback(() => {
    setEditUser(null);
    setModalMode("add");
    setModalError("");
    setModalOpen(true);
  }, []);

  const openEdit = useCallback(
    (u: UserResponse) => {
      setEditUser(u);
      setModalMode("edit");
      setModalError("");
      setModalOpen(true);
    },
    [],
  );

  const handleSave = useCallback(
    async (data: UserFormData) => {
      setModalSaving(true);
      setModalError("");
      try {
        if (modalMode === "add") {
          await admin.createUser({
            email: data.email,
            password: data.password,
            full_name: data.full_name,
            role: data.role,
          });
        } else if (editUser) {
          await admin.updateUser(editUser.user_id, {
            full_name: data.full_name,
            email: data.email,
            role: data.role,
            is_active: data.is_active,
            page_permissions:
              data.role === "general"
                ? data.page_permissions
                : undefined,
          });
        }
        setModalOpen(false);
      } catch (err) {
        setModalError(
          err instanceof Error
            ? err.message
            : "Save failed",
        );
      } finally {
        setModalSaving(false);
      }
    },
    [modalMode, editUser, admin],
  );

  const handleToggle = useCallback(
    async (u: UserResponse) => {
      try {
        if (u.is_active) {
          await admin.deactivateUser(u.user_id);
        } else {
          await admin.reactivateUser(u.user_id);
        }
      } catch (err) {
        alert(
          err instanceof Error
            ? err.message
            : "Action failed",
        );
      }
    },
    [admin],
  );

  const openReset = useCallback(
    (u: UserResponse) => {
      setResetUser(u);
      setResetError("");
      setResetOpen(true);
    },
    [],
  );

  const handleReset = useCallback(
    async (newPassword: string) => {
      if (!resetUser) return;
      setResetSaving(true);
      setResetError("");
      try {
        await admin.resetPassword(
          resetUser.user_id,
          newPassword,
        );
        setResetOpen(false);
      } catch (err) {
        setResetError(
          err instanceof Error
            ? err.message
            : "Reset failed",
        );
      } finally {
        setResetSaving(false);
      }
    },
    [resetUser, admin],
  );

  // Dynamic columns with action buttons.
  const userCols: Column<UserResponse>[] = useMemo(
    () => [
      { key: "full_name", label: "Name" },
      { key: "email", label: "Email" },
      {
        key: "role",
        label: "Role",
        render: (r) => roleBadge(r.role),
      },
      {
        key: "is_active",
        label: "Status",
        render: (r) => statusBadge(r.is_active),
      },
      {
        key: "created_at",
        label: "Created",
        render: (r) => fmtDate(r.created_at),
      },
      {
        key: "last_login_at",
        label: "Last Login",
        render: (r) => fmtDate(r.last_login_at),
      },
      {
        key: "user_id",
        label: "Actions",
        sortable: false,
        render: (r) => (
          <div
            className="flex gap-1"
            data-testid={`admin-user-row-${r.user_id}`}
          >
            <button
              data-testid={`admin-user-edit-${r.user_id}`}
              onClick={() => openEdit(r)}
              className="px-2 py-1 text-xs rounded bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400 hover:bg-indigo-200 dark:hover:bg-indigo-900/50 transition-colors"
            >
              Edit
            </button>
            <button
              data-testid={`admin-user-reset-${r.user_id}`}
              onClick={() => openReset(r)}
              className="px-2 py-1 text-xs rounded bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-900/50 transition-colors"
            >
              Reset Pwd
            </button>
            <button
              data-testid={`admin-user-toggle-${r.user_id}`}
              onClick={() =>
                r.is_active
                  ? setDeactivateUser(r)
                  : handleToggle(r)
              }
              className={`px-2 py-1 text-xs rounded transition-colors ${
                r.is_active
                  ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50"
                  : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 hover:bg-emerald-200 dark:hover:bg-emerald-900/50"
              }`}
            >
              {r.is_active
                ? "Deactivate"
                : "Reactivate"}
            </button>
          </div>
        ),
      },
    ],
    [openEdit, openReset, handleToggle],
  );

  if (admin.loading) return <WidgetSkeleton />;
  if (admin.error)
    return <WidgetError message={admin.error} />;

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-gray-600 dark:text-gray-300">
            All Accounts ({admin.users.length})
          </h2>
          <input
            type="text"
            data-testid="admin-users-search"
            value={search}
            onChange={(e) =>
              setSearch(e.target.value)
            }
            placeholder="Search name, email, role\u2026"
            className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-1.5 text-sm text-gray-700 dark:text-gray-200 w-56 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
          />
        </div>
        <button
          data-testid="admin-users-add-btn"
          onClick={openAdd}
          className="px-3 py-1.5 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors"
        >
          + Add User
        </button>
      </div>

      {/* Users table */}
      <div data-testid="admin-users-table">
      <InsightsTable<UserResponse>
        columns={userCols}
        rows={filtered}
        defaultSort={{
          col: "full_name",
          dir: "asc",
        }}
        onDownload={(r) =>
          downloadCsv(r, userCsvCols, "users")
        }
      />
      </div>

      {/* Modals */}
      <UserModal
        isOpen={modalOpen}
        mode={modalMode}
        user={editUser}
        saving={modalSaving}
        error={modalError}
        onClose={() => setModalOpen(false)}
        onSave={handleSave}
      />
      <ResetPasswordModal
        isOpen={resetOpen}
        userName={
          resetUser?.full_name ?? ""
        }
        saving={resetSaving}
        error={resetError}
        onClose={() => setResetOpen(false)}
        onSave={handleReset}
      />
      <ConfirmDialog
        open={deactivateUser !== null}
        title="Deactivate User"
        message={
          deactivateUser
            ? `Deactivate ${deactivateUser.full_name}? They will lose access immediately.`
            : ""
        }
        confirmLabel="Deactivate"
        variant="danger"
        onConfirm={() => {
          if (deactivateUser) {
            handleToggle(deactivateUser);
          }
          setDeactivateUser(null);
        }}
        onCancel={() => setDeactivateUser(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------
// Audit Log Tab
// ---------------------------------------------------------------

function AuditLogTab() {
  const audit = useAdminAudit();
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return audit.events;
    const q = search.toLowerCase();
    return audit.events.filter(
      (e) =>
        e.event_type.toLowerCase().includes(q) ||
        (e.actor_user_id ?? "")
          .toLowerCase()
          .includes(q) ||
        (e.target_user_id ?? "")
          .toLowerCase()
          .includes(q) ||
        parseMetadata(e.metadata)
          .toLowerCase()
          .includes(q),
    );
  }, [audit.events, search]);

  if (audit.loading) return <WidgetSkeleton />;
  if (audit.error)
    return <WidgetError message={audit.error} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-medium text-gray-600 dark:text-gray-300">
          Audit Log ({audit.events.length} events)
        </h2>
        <input
          type="text"
          data-testid="admin-audit-search"
          value={search}
          onChange={(e) =>
            setSearch(e.target.value)
          }
          placeholder="Search events\u2026"
          className="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-1.5 text-sm text-gray-700 dark:text-gray-200 w-56 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
        />
      </div>

      <div data-testid="admin-audit-table">
      <InsightsTable<AuditEvent>
        columns={auditCols}
        rows={filtered}
        defaultSort={{
          col: "event_timestamp",
          dir: "desc",
        }}
        onDownload={(r) =>
          downloadCsv(
            r, auditCsvCols, "audit-log",
          )
        }
      />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Daily Token Budget Card
interface BudgetModel {
  total: number;
  requests: number;
  limit: number;
}
interface DailyBudgetData {
  date: string;
  daily_limit: number;
  total_tokens: number;
  remaining_tokens: number;
  usage_pct: number;
  by_model: Record<string, BudgetModel>;
  estimated_queries_remaining: number;
  reset_time_utc: string;
}

function DailyTokenBudgetCard() {
  const [data, setData] = useState<
    DailyBudgetData | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { apiFetch } = await import(
          "@/lib/apiFetch"
        );
        const { API_URL } = await import(
          "@/lib/config"
        );
        const res = await apiFetch(
          `${API_URL}/admin/daily-budget`,
        );
        if (!res.ok) throw new Error("Failed");
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch (e) {
        if (!cancelled)
          setError(
            e instanceof Error
              ? e.message
              : "Load failed",
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <WidgetSkeleton />;
  if (error || !data)
    return (
      <WidgetError
        message={error || "No data"}
      />
    );

  const pct = data.usage_pct;
  const barColor =
    pct > 85
      ? "bg-red-500"
      : pct > 60
        ? "bg-amber-500"
        : "bg-emerald-500";
  const textColor =
    pct > 85
      ? "text-red-600 dark:text-red-400"
      : pct > 60
        ? "text-amber-600 dark:text-amber-400"
        : "text-emerald-600 dark:text-emerald-400";

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
          Daily Token Budget
        </h3>
        <span className="text-xs text-gray-400">
          Resets{" "}
          {new Date(
            data.reset_time_utc,
          ).toLocaleTimeString()}
        </span>
      </div>

      {/* Progress bar */}
      <div className="w-full h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden mb-2">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{
            width: `${Math.min(pct, 100)}%`,
          }}
        />
      </div>
      <div className="flex justify-between text-xs mb-4">
        <span className={textColor}>
          {pct}% used
        </span>
        <span className="text-gray-500 dark:text-gray-400">
          {data.total_tokens.toLocaleString()} /{" "}
          {data.daily_limit.toLocaleString()} tokens
        </span>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="rounded-lg bg-gray-50 dark:bg-gray-800 p-2.5">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Remaining
          </p>
          <p className="text-lg font-semibold text-gray-700 dark:text-gray-200">
            {data.remaining_tokens.toLocaleString()}
          </p>
        </div>
        <div className="rounded-lg bg-gray-50 dark:bg-gray-800 p-2.5">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Est. Queries Left
          </p>
          <p className="text-lg font-semibold text-gray-700 dark:text-gray-200">
            ~
            {data.estimated_queries_remaining.toLocaleString()}
          </p>
        </div>
      </div>

      {/* Per-model breakdown */}
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
          Per-Model Breakdown
        </p>
        {Object.entries(data.by_model).map(
          ([model, info]) => {
            const modelPct =
              info.limit > 0
                ? Math.round(
                    (info.total / info.limit) *
                      100,
                  )
                : 0;
            const short =
              model.split("/").pop() ?? model;
            return (
              <div
                key={model}
                className="flex items-center gap-2 text-xs"
              >
                <span className="w-32 truncate text-gray-600 dark:text-gray-400">
                  {short}
                </span>
                <div className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full">
                  <div
                    className={`h-full rounded-full ${
                      modelPct > 85
                        ? "bg-red-500"
                        : modelPct > 60
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                    }`}
                    style={{
                      width: `${Math.min(modelPct, 100)}%`,
                    }}
                  />
                </div>
                <span className="w-20 text-right text-gray-500 dark:text-gray-400">
                  {info.total.toLocaleString()} /{" "}
                  {(
                    info.limit / 1000
                  ).toFixed(0)}
                  K
                </span>
              </div>
            );
          },
        )}
      </div>
    </div>
  );
}

// LLM Observability Tab
// ---------------------------------------------------------------

/** Shorten model name for display. */
function shortModel(m: string): string {
  const parts = m.split("/");
  let name = parts[parts.length - 1];
  name = name
    .replace("-instruct", "")
    .replace("-16e-instruct", "")
    .replace("-versatile", "");
  return name;
}

/** Progress bar color based on utilization. */
function budgetColor(
  used: number,
  limit: number,
): string {
  if (limit <= 0) return "bg-gray-400";
  const pct = used / limit;
  if (pct >= 0.8) return "bg-red-500";
  if (pct >= 0.5) return "bg-amber-500";
  return "bg-emerald-500";
}

/** Parse "1234/8000" into [used, limit]. */
function parseBudget(
  s: string,
): [number, number] {
  const parts = s.split("/");
  return [
    parseInt(parts[0] ?? "0", 10) || 0,
    parseInt(parts[1] ?? "0", 10) || 0,
  ];
}

const STATUS_ICON: Record<string, string> = {
  healthy: "\u25CF",
  degraded: "\u25B2",
  down: "\u2715",
  disabled: "\u2298",
};

const STATUS_COLOR: Record<string, string> = {
  healthy:
    "text-emerald-600 dark:text-emerald-400",
  degraded:
    "text-amber-600 dark:text-amber-400",
  down: "text-red-600 dark:text-red-400",
  disabled:
    "text-gray-400 dark:text-gray-500",
};

const STATUS_BORDER: Record<string, string> = {
  healthy:
    "border-l-emerald-500",
  degraded:
    "border-l-amber-500",
  down: "border-l-red-500",
  disabled:
    "border-l-gray-400",
};

const STATUS_BADGE: Record<string, string> = {
  healthy:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  degraded:
    "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  down:
    "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  disabled:
    "bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400",
};

const cascadeCols: Column<CascadeEvent>[] = [
  {
    key: "timestamp",
    label: "Time",
    render: (r) => {
      const d = new Date(r.timestamp * 1000);
      return d.toLocaleTimeString("en-US", {
        hour12: false,
        timeZone: "UTC",
      }) + " UTC";
    },
  },
  {
    key: "from_model",
    label: "From",
    render: (r) => shortModel(r.from_model),
  },
  {
    key: "to_model",
    label: "To",
    render: (r) =>
      r.to_model
        ? shortModel(r.to_model)
        : "\u2014",
  },
  {
    key: "reason",
    label: "Reason",
    render: (r) => (
      <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300">
        {r.reason}
      </span>
    ),
  },
];

function ObservabilityTab() {
  const obs = useObservability();

  if (obs.loading) return <WidgetSkeleton />;
  if (obs.error)
    return <WidgetError message={obs.error} />;

  const stats = obs.metrics?.cascade_stats;
  const models = obs.metrics?.models ?? {};
  const tiers =
    obs.health?.health.tiers ?? [];
  const summary = obs.health?.health.summary;
  const cascadeLog = [
    ...(stats?.cascade_log ?? []),
  ].reverse().slice(0, 25);

  const totalTokens =
    stats?.total_tokens ?? 0;
  const totalPrompt =
    stats?.total_prompt_tokens ?? 0;
  const totalCompletion =
    stats?.total_completion_tokens ?? 0;
  const promptPct =
    totalTokens > 0
      ? Math.round(
          (totalPrompt / totalTokens) * 100,
        )
      : 0;

  const handleToggle = async (
    model: string,
    currentStatus: string,
  ) => {
    try {
      await obs.toggleTier(
        model,
        currentStatus === "disabled",
      );
    } catch (err) {
      alert(
        err instanceof Error
          ? err.message
          : "Toggle failed",
      );
    }
  };

  return (
    <div className="space-y-6">
      {/* Daily Token Budget */}
      <DailyTokenBudgetCard />

      {/* Summary cards — 5 columns */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <div
          data-testid="admin-summary-requests"
          className="rounded-xl bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800 p-4"
        >
          <p className="text-xs font-medium text-indigo-500 dark:text-indigo-400">
            Total Requests
          </p>
          <p className="text-2xl font-semibold text-indigo-700 dark:text-indigo-300 mt-1">
            {(
              stats?.requests_total ?? 0
            ).toLocaleString()}
          </p>
        </div>
        <div
          data-testid="admin-summary-tokens"
          className="rounded-xl bg-violet-50 dark:bg-violet-900/20 border border-violet-200 dark:border-violet-800 p-4"
        >
          <p className="text-xs font-medium text-violet-500 dark:text-violet-400">
            Total Tokens
          </p>
          <p className="text-2xl font-semibold text-violet-700 dark:text-violet-300 mt-1">
            {totalTokens.toLocaleString()}
          </p>
        </div>
        <div
          data-testid="admin-summary-input"
          className="rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-4"
        >
          <p className="text-xs font-medium text-blue-500 dark:text-blue-400">
            Input Tokens
          </p>
          <p className="text-2xl font-semibold text-blue-700 dark:text-blue-300 mt-1">
            {totalPrompt.toLocaleString()}
          </p>
          <p className="text-xs text-blue-400 dark:text-blue-500 mt-0.5">
            {promptPct}% of total
          </p>
        </div>
        <div
          data-testid="admin-summary-output"
          className="rounded-xl bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 p-4"
        >
          <p className="text-xs font-medium text-emerald-500 dark:text-emerald-400">
            Output Tokens
          </p>
          <p className="text-2xl font-semibold text-emerald-700 dark:text-emerald-300 mt-1">
            {totalCompletion.toLocaleString()}
          </p>
          <p className="text-xs text-emerald-400 dark:text-emerald-500 mt-0.5">
            {100 - promptPct}% of total
          </p>
        </div>
        <div
          data-testid="admin-summary-cascades"
          className="rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-4"
        >
          <p className="text-xs font-medium text-amber-500 dark:text-amber-400">
            Cascades
          </p>
          <p className="text-2xl font-semibold text-amber-700 dark:text-amber-300 mt-1">
            {stats?.cascade_count ?? 0}
          </p>
          <p className="text-xs text-amber-400 dark:text-amber-500 mt-0.5">
            {stats?.compression_count ?? 0}{" "}
            compressions
          </p>
        </div>
      </div>

      {/* Tier Health */}
      {tiers.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-medium text-gray-600 dark:text-gray-300">
              Tier Health
            </h2>
            {summary && (
              <div className="flex gap-2 text-xs">
                <span className="text-emerald-600 dark:text-emerald-400">
                  {summary.healthy} Healthy
                </span>
                {summary.degraded > 0 && (
                  <span className="text-amber-600 dark:text-amber-400">
                    {summary.degraded} Degraded
                  </span>
                )}
                {summary.down > 0 && (
                  <span className="text-red-600 dark:text-red-400">
                    {summary.down} Down
                  </span>
                )}
                {summary.disabled > 0 && (
                  <span className="text-gray-400">
                    {summary.disabled} Disabled
                  </span>
                )}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {tiers.map((t) => (
              <div
                key={t.model}
                data-testid={`admin-tier-card-${t.model}`}
                className={`rounded-lg border border-gray-200 dark:border-gray-700 border-l-4 ${STATUS_BORDER[t.status] ?? ""} bg-white dark:bg-gray-900 p-3 space-y-2`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
                    {shortModel(t.model)}
                  </span>
                  <button
                    data-testid={`admin-tier-toggle-${t.model}`}
                    onClick={() =>
                      handleToggle(
                        t.model,
                        t.status,
                      )
                    }
                    className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                    title={
                      t.status === "disabled"
                        ? "Enable tier"
                        : "Disable tier"
                    }
                  >
                    {t.status === "disabled"
                      ? "Enable"
                      : "Disable"}
                  </button>
                </div>
                <div className="flex items-center gap-1.5">
                  <span
                    className={`text-sm ${STATUS_COLOR[t.status] ?? ""}`}
                  >
                    {STATUS_ICON[t.status] ?? "?"}
                  </span>
                  <span
                    className={`text-xs font-medium px-1.5 py-0.5 rounded ${STATUS_BADGE[t.status] ?? ""}`}
                  >
                    {t.status}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
                  <span>
                    Fail (5m): {t.failures_5m}
                  </span>
                  <span>
                    OK (5m): {t.successes_5m}
                  </span>
                  <span>
                    Cascades: {t.cascade_count}
                  </span>
                  <span>
                    Avg: {t.latency.avg_ms}ms
                  </span>
                  <span>
                    p95: {t.latency.p95_ms}ms
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Model Budget & Usage */}
      {Object.keys(models).length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-gray-600 dark:text-gray-300">
            Per-Model Budget & Usage
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Object.entries(models).map(
              ([name, budget]) => {
                const [tpmUsed, tpmLim] =
                  parseBudget(budget.tpm);
                const [rpmUsed, rpmLim] =
                  parseBudget(budget.rpm);
                const [tpdUsed, tpdLim] =
                  parseBudget(budget.tpd);
                const [rpdUsed, rpdLim] =
                  parseBudget(budget.rpd);
                const reqCount =
                  stats?.requests_by_model?.[
                    name
                  ] ?? 0;
                const inTok =
                  stats
                    ?.prompt_tokens_by_model?.[
                    name
                  ] ?? 0;
                const outTok =
                  stats
                    ?.completion_tokens_by_model?.[
                    name
                  ] ?? 0;

                return (
                  <div
                    key={name}
                    data-testid={`admin-budget-card-${name}`}
                    className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 space-y-3"
                  >
                    {/* Header */}
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
                        {shortModel(name)}
                      </p>
                      <span className="text-xs font-mono text-gray-400 dark:text-gray-500">
                        {reqCount.toLocaleString()}{" "}
                        req
                      </span>
                    </div>

                    {/* Token split */}
                    {(inTok > 0 ||
                      outTok > 0) && (
                      <div className="flex gap-3 text-xs">
                        <span className="text-blue-600 dark:text-blue-400">
                          In:{" "}
                          {inTok.toLocaleString()}
                        </span>
                        <span className="text-emerald-600 dark:text-emerald-400">
                          Out:{" "}
                          {outTok.toLocaleString()}
                        </span>
                      </div>
                    )}

                    {/* Rate bars */}
                    <div className="space-y-2">
                      {/* TPM */}
                      <div>
                        <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
                          <span>TPM</span>
                          <span>
                            {budget.tpm}
                          </span>
                        </div>
                        <div className="h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${budgetColor(tpmUsed, tpmLim)}`}
                            style={{
                              width: `${tpmLim > 0 ? Math.min(100, (tpmUsed / tpmLim) * 100) : 0}%`,
                            }}
                          />
                        </div>
                      </div>
                      {/* TPD */}
                      <div>
                        <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
                          <span>TPD</span>
                          <span>
                            {budget.tpd}
                          </span>
                        </div>
                        <div className="h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${budgetColor(tpdUsed, tpdLim)}`}
                            style={{
                              width: `${tpdLim > 0 ? Math.min(100, (tpdUsed / tpdLim) * 100) : 0}%`,
                            }}
                          />
                        </div>
                      </div>
                      {/* RPM */}
                      <div>
                        <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
                          <span>RPM</span>
                          <span>
                            {budget.rpm}
                          </span>
                        </div>
                        <div className="h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${budgetColor(rpmUsed, rpmLim)}`}
                            style={{
                              width: `${rpmLim > 0 ? Math.min(100, (rpmUsed / rpmLim) * 100) : 0}%`,
                            }}
                          />
                        </div>
                      </div>
                      {/* RPD */}
                      <div>
                        <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mb-0.5">
                          <span>RPD</span>
                          <span>
                            {budget.rpd}
                          </span>
                        </div>
                        <div className="h-1.5 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${budgetColor(rpdUsed, rpdLim)}`}
                            style={{
                              width: `${rpdLim > 0 ? Math.min(100, (rpdUsed / rpdLim) * 100) : 0}%`,
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              },
            )}
          </div>
        </div>
      )}

      {/* Recent Cascade Events */}
      {cascadeLog.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-gray-600 dark:text-gray-300">
            Recent Cascade Events
          </h2>
          <div data-testid="admin-cascade-table">
          <InsightsTable<CascadeEvent>
            columns={cascadeCols}
            rows={cascadeLog}
            pageSize={10}
          />
          </div>
        </div>
      )}

      {/* Empty state */}
      {!stats?.requests_total &&
        tiers.length === 0 &&
        Object.keys(models).length === 0 && (
          <div className="py-12 text-center text-gray-400">
            No LLM activity recorded yet
          </div>
        )}
    </div>
  );
}

// ---------------------------------------------------------------
// Maintenance Tab
// ---------------------------------------------------------------

const RISK_COLORS = {
  none: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  low: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  high: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
} as const;

function RiskBadge({ level }: { level: keyof typeof RISK_COLORS }) {
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${RISK_COLORS[level]}`}>
      {level.charAt(0).toUpperCase() + level.slice(1)} risk
    </span>
  );
}

function MaintenanceTab() {
  const m = useAdminMaintenance();
  const [triageResult, setTriageResult] = useState<{ triage: TriageEntry[]; cleaned: number; dry_run: boolean } | null>(null);
  const [usageResult, setUsageResult] = useState<{ reset_count: number } | null>(null);
  const [usageUsers, setUsageUsers] = useState<UsageUser[] | null>(null);
  const [selectedUsers, setSelectedUsers] = useState<Set<string>>(new Set());
  const [retentionResult, setRetentionResult] = useState<RetentionResult[] | null>(null);
  const [selectedTables, setSelectedTables] = useState<Set<string>>(new Set());
  const [gapResult, setGapResult] = useState<GapResult | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ title: string; message: string; onConfirm: () => void } | null>(null);

  const run = async (key: string, fn: () => Promise<void>) => {
    setLoading(key);
    try { await fn(); } catch { /* shown in result */ }
    setLoading(null);
  };

  return (
    <div className="space-y-4">
      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ""}
        message={confirm?.message ?? ""}
        confirmLabel="Execute"
        variant="warning"
        onConfirm={() => {
          confirm?.onConfirm();
          setConfirm(null);
        }}
        onCancel={() => setConfirm(null)}
      />

      {/* Data Health */}
      <DataHealthPanel />

      {/* Backup Health */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
        <BackupHealthPanel />
      </div>

      {/* Subscription Cleanup */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Razorpay: Triage Orphaned Subscriptions</h3>
          <RiskBadge level="medium" />
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          Scans active Razorpay subscriptions and classifies as <strong>Matched</strong> (current), <strong>Orphaned</strong> (same customer, wrong sub — safe to cancel), or <strong>Unlinked</strong> (no user — manual review).
        </p>
        <div className="flex gap-2 mb-3">
          <button
            onClick={() => run("scan", async () => { setTriageResult(await m.cleanupSubscriptions(true)); })}
            disabled={loading === "scan"}
            className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            {loading === "scan" ? "Scanning\u2026" : "Scan"}
          </button>
          {triageResult?.triage.some((t) => t.classification === "orphaned") && (
            <button
              onClick={() => setConfirm({
                title: "Cancel Orphaned Subscriptions",
                message: `This will cancel ${triageResult.triage.filter((t) => t.classification === "orphaned").length} orphaned subscription(s) in Razorpay. Continue?`,
                onConfirm: () => run("cleanup", async () => { setTriageResult(await m.cleanupSubscriptions(false)); }),
              })}
              disabled={loading === "cleanup"}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {loading === "cleanup" ? "Cleaning\u2026" : "Execute Cleanup"}
            </button>
          )}
        </div>
        {triageResult && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
                  <th className="pb-1 pr-3">Sub ID</th>
                  <th className="pb-1 pr-3">Customer</th>
                  <th className="pb-1 pr-3">Status</th>
                  <th className="pb-1 pr-3">Classification</th>
                  <th className="pb-1">Action</th>
                </tr>
              </thead>
              <tbody>
                {triageResult.triage.map((t) => (
                  <tr key={t.sub_id} className="border-b border-gray-100 dark:border-gray-800">
                    <td className="py-1 pr-3 font-mono">{t.sub_id.slice(0, 20)}</td>
                    <td className="py-1 pr-3 font-mono">{t.customer_id.slice(0, 20)}</td>
                    <td className="py-1 pr-3">{t.status}</td>
                    <td className="py-1 pr-3">
                      <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                        t.classification === "matched" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : t.classification === "orphaned" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                        : "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400"
                      }`}>
                        {t.classification}
                      </span>
                    </td>
                    <td className="py-1">{t.action}</td>
                  </tr>
                ))}
                {triageResult.triage.length === 0 && (
                  <tr><td colSpan={5} className="py-2 text-center text-gray-400">No active subscriptions found</td></tr>
                )}
              </tbody>
            </table>
            {!triageResult.dry_run && <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400 font-medium">Cleaned: {triageResult.cleaned} subscription(s)</p>}
          </div>
        )}
      </div>

      {/* Usage Reset */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Reset Monthly Usage Counters</h3>
          <RiskBadge level="low" />
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          Scan users to see usage stats, then reset individually, selected, or all at once.
        </p>
        <div className="flex gap-2 mb-3">
          <button
            onClick={() => run("usage-scan", async () => {
              const r = await m.getUsageStats();
              setUsageUsers(r.users);
              setSelectedUsers(new Set());
              setUsageResult(null);
            })}
            disabled={loading === "usage-scan"}
            className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            {loading === "usage-scan" ? "Scanning\u2026" : "Scan"}
          </button>
          {usageUsers && selectedUsers.size > 0 && (
            <button
              onClick={() => run("usage-selected", async () => {
                const r = await m.resetSelectedUsage([...selectedUsers]);
                setUsageResult(r);
                const fresh = await m.getUsageStats();
                setUsageUsers(fresh.users);
                setSelectedUsers(new Set());
              })}
              disabled={loading === "usage-selected"}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {loading === "usage-selected" ? "Resetting\u2026" : `Reset Selected (${selectedUsers.size})`}
            </button>
          )}
          <button
            onClick={() => setConfirm({
              title: "Reset All Usage Counters",
              message: "This will zero the monthly usage count for ALL users. Continue?",
              onConfirm: () => run("usage-all", async () => {
                const r = await m.resetUsage();
                setUsageResult(r);
                if (usageUsers) {
                  const fresh = await m.getUsageStats();
                  setUsageUsers(fresh.users);
                }
              }),
            })}
            disabled={loading === "usage-all"}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {loading === "usage-all" ? "Resetting\u2026" : "Reset All"}
          </button>
        </div>
        {usageResult && (
          <p className="mb-2 text-xs text-emerald-600 dark:text-emerald-400 font-medium">Reset: {usageResult.reset_count} user(s)</p>
        )}
        {usageUsers && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
                  <th className="pb-1 pr-2 w-8">
                    <input
                      type="checkbox"
                      checked={usageUsers.length > 0 && selectedUsers.size === usageUsers.filter((u) => u.monthly_usage_count > 0).length}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedUsers(new Set(usageUsers.filter((u) => u.monthly_usage_count > 0).map((u) => u.user_id)));
                        } else {
                          setSelectedUsers(new Set());
                        }
                      }}
                      className="rounded border-gray-300 dark:border-gray-600"
                    />
                  </th>
                  <th className="pb-1 pr-3">User</th>
                  <th className="pb-1 pr-3">Tier</th>
                  <th className="pb-1 pr-3">Usage</th>
                  <th className="pb-1">Action</th>
                </tr>
              </thead>
              <tbody>
                {usageUsers.map((u) => (
                  <tr key={u.user_id} className="border-b border-gray-100 dark:border-gray-800">
                    <td className="py-1 pr-2">
                      {u.monthly_usage_count > 0 && (
                        <input
                          type="checkbox"
                          checked={selectedUsers.has(u.user_id)}
                          onChange={(e) => {
                            const next = new Set(selectedUsers);
                            if (e.target.checked) next.add(u.user_id);
                            else next.delete(u.user_id);
                            setSelectedUsers(next);
                          }}
                          className="rounded border-gray-300 dark:border-gray-600"
                        />
                      )}
                    </td>
                    <td className="py-1 pr-3">
                      <div className="font-medium text-gray-900 dark:text-gray-100">{u.full_name || "\u2014"}</div>
                      <div className="text-gray-400">{u.email}</div>
                    </td>
                    <td className="py-1 pr-3 capitalize">{u.subscription_tier}</td>
                    <td className="py-1 pr-3 font-mono">{u.monthly_usage_count}</td>
                    <td className="py-1">
                      {u.monthly_usage_count > 0 && (
                        <button
                          onClick={() => run(`reset-${u.user_id}`, async () => {
                            await m.resetSelectedUsage([u.user_id]);
                            const fresh = await m.getUsageStats();
                            setUsageUsers(fresh.users);
                          })}
                          disabled={loading === `reset-${u.user_id}`}
                          className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300 disabled:opacity-50"
                        >
                          {loading === `reset-${u.user_id}` ? "Resetting\u2026" : "Reset"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {usageUsers.length === 0 && (
                  <tr><td colSpan={5} className="py-2 text-center text-gray-400">No users found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Data Retention */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Iceberg Data Retention Cleanup</h3>
          <RiskBadge level="high" />
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          Scan tables to see what would be deleted, then clean individually, selected, or all at once. Protected tables (stocks.registry) are never touched.
        </p>
        <div className="flex gap-2 mb-3">
          <button
            onClick={() => run("retention-scan", async () => {
              const r = await m.runRetention(true);
              setRetentionResult(r.results);
              setSelectedTables(new Set());
            })}
            disabled={loading === "retention-scan"}
            className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            {loading === "retention-scan" ? "Scanning\u2026" : "Scan"}
          </button>
          {retentionResult && selectedTables.size > 0 && (
            <button
              onClick={() => setConfirm({
                title: "Delete Selected Tables",
                message: `This will permanently delete old rows from ${selectedTables.size} table(s). This cannot be undone. Continue?`,
                onConfirm: () => run("retention-selected", async () => {
                  const r = await m.retainSelected([...selectedTables]);
                  setRetentionResult(r.results);
                  setSelectedTables(new Set());
                }),
              })}
              disabled={loading === "retention-selected"}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {loading === "retention-selected" ? "Deleting\u2026" : `Delete Selected (${selectedTables.size})`}
            </button>
          )}
          <button
            onClick={() => setConfirm({
              title: "Delete All — Data Retention",
              message: "This will permanently delete old rows from ALL tables. This cannot be undone. Continue?",
              onConfirm: () => run("retention-all", async () => {
                const r = await m.runRetention(false);
                setRetentionResult(r.results);
                setSelectedTables(new Set());
              }),
            })}
            disabled={loading === "retention-all"}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
          >
            {loading === "retention-all" ? "Deleting\u2026" : "Delete All"}
          </button>
        </div>
        {retentionResult && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
                  <th className="pb-1 pr-2 w-8">
                    <input
                      type="checkbox"
                      checked={retentionResult.length > 0 && selectedTables.size === retentionResult.filter((r) => r.rows_deleted > 0).length}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedTables(new Set(retentionResult.filter((r) => r.rows_deleted > 0).map((r) => r.table)));
                        } else {
                          setSelectedTables(new Set());
                        }
                      }}
                      className="rounded border-gray-300 dark:border-gray-600"
                    />
                  </th>
                  <th className="pb-1 pr-3">Table</th>
                  <th className="pb-1 pr-3">Cutoff</th>
                  <th className="pb-1 pr-3">Rows</th>
                  <th className="pb-1 pr-3">Would Delete</th>
                  <th className="pb-1">Action</th>
                </tr>
              </thead>
              <tbody>
                {retentionResult.map((r) => (
                  <tr key={r.table} className="border-b border-gray-100 dark:border-gray-800">
                    <td className="py-1 pr-2">
                      {r.rows_deleted > 0 && r.dry_run && (
                        <input
                          type="checkbox"
                          checked={selectedTables.has(r.table)}
                          onChange={(e) => {
                            const next = new Set(selectedTables);
                            if (e.target.checked) next.add(r.table);
                            else next.delete(r.table);
                            setSelectedTables(next);
                          }}
                          className="rounded border-gray-300 dark:border-gray-600"
                        />
                      )}
                    </td>
                    <td className="py-1 pr-3 font-mono">{r.table}</td>
                    <td className="py-1 pr-3">{r.cutoff_date}</td>
                    <td className="py-1 pr-3">{r.rows_before}</td>
                    <td className="py-1 pr-3">{r.dry_run ? `(${r.rows_deleted})` : r.rows_deleted}</td>
                    <td className="py-1">
                      {r.error ? (
                        <span className="text-red-500">{r.error}</span>
                      ) : r.dry_run && r.rows_deleted > 0 ? (
                        <button
                          onClick={() => setConfirm({
                            title: `Delete from ${r.table}`,
                            message: `Delete ${r.rows_deleted} rows older than ${r.cutoff_date} from ${r.table}?`,
                            onConfirm: () => run(`retain-${r.table}`, async () => {
                              await m.retainSelected([r.table]);
                              const fresh = await m.runRetention(true);
                              setRetentionResult(fresh.results);
                            }),
                          })}
                          disabled={loading === `retain-${r.table}`}
                          className="text-xs text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-300 disabled:opacity-50"
                        >
                          {loading === `retain-${r.table}` ? "Deleting\u2026" : "Delete"}
                        </button>
                      ) : r.dry_run ? (
                        <span className="text-gray-400">clean</span>
                      ) : (
                        <span className="text-emerald-600 dark:text-emerald-400">done</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Gap Analysis */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Query Gap Analysis</h3>
          <RiskBadge level="none" />
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          Read-only analysis of unresolved data gaps, external API usage, and local data sufficiency.
        </p>
        <button
          onClick={() => run("gaps", async () => { setGapResult(await m.analyzeGaps()); })}
          disabled={loading === "gaps"}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {loading === "gaps" ? "Analyzing\u2026" : "Analyze"}
        </button>
        {gapResult && (
          <div className="mt-3 space-y-2 text-xs">
            <div>
              <span className="font-medium text-gray-700 dark:text-gray-300">Top gap tickers: </span>
              <span className="text-gray-500 dark:text-gray-400">{gapResult.top_gap_tickers?.join(", ") || "None"}</span>
            </div>
            <div>
              <span className="font-medium text-gray-700 dark:text-gray-300">Local sufficiency: </span>
              <span className="text-gray-500 dark:text-gray-400">{((gapResult.local_sufficiency_rate ?? 0) * 100).toFixed(1)}%</span>
            </div>
            {gapResult.external_api_usage && (
              <div>
                <span className="font-medium text-gray-700 dark:text-gray-300">External API calls: </span>
                <span className="text-gray-500 dark:text-gray-400">
                  {Object.entries(gapResult.external_api_usage).map(([k, v]) => `${k}: ${v}`).join(", ")}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Transactions Tab
// ---------------------------------------------------------------

function TransactionsTab() {
  const m = useAdminMaintenance();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [txns, setTxns] = useState<
    Record<string, any>[]
  >([]);
  const [loading, setLoading] = useState(false);
  const [gwFilter, setGwFilter] =
    useState<string>("");

  const fetchTxns = async () => {
    setLoading(true);
    try {
      const r =
        await m.getPaymentTransactions(
          undefined,
          gwFilter || undefined,
        );
      setTxns(r.transactions || []);
    } catch {
      /* ignore */
    }
    setLoading(false);
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { fetchTxns(); }, [gwFilter]);

  const statusColor = (s: string) =>
    s === "success"
      ? "text-emerald-600 dark:text-emerald-400"
      : s === "failed"
        ? "text-red-600 dark:text-red-400"
        : "text-amber-600 dark:text-amber-400";

  const gwBadge = (gw: string) => {
    const cls =
      gw === "stripe"
        ? "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400"
        : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
    return (
      <span
        className={`px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}
      >
        {gw}
      </span>
    );
  };

  const sourceBadge = (evt: string) => {
    const isUser =
      evt.startsWith("user_") ||
      evt === "upgrade";
    const cls = isUser
      ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400"
      : "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400";
    return (
      <span
        className={`px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}
      >
        {isUser ? "User" : "Webhook"}
      </span>
    );
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const txnCols: Column<Record<string, any>>[] = [
    {
      key: "created_at",
      label: "Date",
      render: (r) =>
        String(r.created_at ?? "").slice(0, 19),
    },
    {
      key: "user_id",
      label: "User",
      render: (r) => (
        <code className="text-xs font-mono">
          {String(r.user_id ?? "").slice(0, 8)}
        </code>
      ),
    },
    {
      key: "user_name",
      label: "Name",
      render: (r) => (
        <div>
          <div className="font-medium text-gray-900 dark:text-gray-100">
            {String(r.user_name ?? "")}
          </div>
          <div className="text-gray-400 text-[10px]">
            {String(r.user_email ?? "")}
          </div>
        </div>
      ),
    },
    {
      key: "gateway",
      label: "Gateway",
      render: (r) =>
        gwBadge(String(r.gateway ?? "")),
    },
    {
      key: "event_type",
      label: "Event",
    },
    {
      key: "source",
      label: "Source",
      sortable: false,
      render: (r) =>
        sourceBadge(
          String(r.event_type ?? ""),
        ),
    },
    {
      key: "amount",
      label: "Amount",
      numeric: true,
      render: (r) =>
        r.amount
          ? `${r.currency} ${r.amount}`
          : "\u2014",
    },
    {
      key: "tier_before",
      label: "Tier Change",
      render: (r) =>
        r.tier_before && r.tier_after
          ? `${r.tier_before} \u2192 ${r.tier_after}`
          : "\u2014",
    },
    {
      key: "status",
      label: "Status",
      render: (r) => (
        <span
          className={`font-medium ${statusColor(String(r.status ?? ""))}`}
        >
          {String(r.status ?? "")}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select
          value={gwFilter}
          onChange={(e) =>
            setGwFilter(e.target.value)
          }
          className="text-xs border border-gray-300
            dark:border-gray-600 bg-white
            dark:bg-gray-800 rounded-lg px-2
            py-1.5 text-gray-700
            dark:text-gray-300"
        >
          <option value="">All Gateways</option>
          <option value="razorpay">
            Razorpay
          </option>
          <option value="stripe">Stripe</option>
        </select>
        <button
          onClick={fetchTxns}
          disabled={loading}
          className="text-xs px-3 py-1.5
            rounded-lg border border-gray-300
            dark:border-gray-600 text-gray-700
            dark:text-gray-300 hover:bg-gray-50
            dark:hover:bg-gray-700
            disabled:opacity-50"
        >
          {loading ? "Loading\u2026" : "Refresh"}
        </button>
      </div>

      {loading && <WidgetSkeleton />}

      {!loading && (
        <InsightsTable<Record<string, any>>
          columns={txnCols}
          rows={txns}
          defaultSort={{
            col: "created_at",
            dir: "desc",
          }}
          onDownload={(r) =>
            downloadCsv(
              r, txnCsvCols, "transactions",
            )
          }
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Main page
// ---------------------------------------------------------------

type AdminTab =
  | "users"
  | "audit"
  | "observability"
  | "maintenance"
  | "transactions"
  | "scheduler";

function AdminPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [tab, setTab] = useState<AdminTab>(
    (searchParams.get("tab") as AdminTab) ?? "users",
  );

  const handleTabChange = useCallback(
    (t: AdminTab) => {
      setTab(t);
      router.replace(`/admin?tab=${t}`, {
        scroll: false,
      });
    },
    [router],
  );

  return (
    <div className="space-y-6 p-4 sm:p-6">
      {/* Tab bar */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 pb-px">
        {(
          [
            { id: "users", label: "Users" },
            { id: "audit", label: "Audit Log" },
            {
              id: "observability",
              label: "LLM Observability",
            },
            {
              id: "maintenance",
              label: "Maintenance",
            },
            {
              id: "transactions",
              label: "Transactions",
            },
            {
              id: "scheduler",
              label: "Scheduler",
            },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            data-testid={`admin-tab-${t.id}`}
            onClick={() => handleTabChange(t.id)}
            className={`
              whitespace-nowrap px-3 py-2 text-sm
              font-medium rounded-t-lg transition-colors
              ${
                tab === t.id
                  ? "text-indigo-600 dark:text-indigo-400 border-b-2 border-indigo-600 dark:border-indigo-400 -mb-px"
                  : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
              }
            `}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="min-h-[400px]">
        {tab === "users" && <UsersTab />}
        {tab === "audit" && <AuditLogTab />}
        {tab === "observability" && (
          <ObservabilityTab />
        )}
        {tab === "maintenance" && (
          <MaintenanceTab />
        )}
        {tab === "transactions" && (
          <TransactionsTab />
        )}
        {tab === "scheduler" && <SchedulerTab />}
      </div>
    </div>
  );
}

export default function AdminPage() {
  return (
    <Suspense fallback={null}>
      <AdminPageInner />
    </Suspense>
  );
}
