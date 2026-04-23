"use client";
/**
 * BackupHealthPanel — readonly backup status
 * dashboard with health badge, backup list,
 * and expandable folder browser.
 */

import {
  useState,
  useEffect,
  useCallback,
} from "react";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

interface BackupEntry {
  date: string;
  path: string;
  size_mb: number;
  age_hours: number;
  has_catalog: boolean;
}

interface BackupHealth {
  status: "healthy" | "stale" | "critical" | "missing";
  latest_date: string | null;
  age_hours: number | null;
  backup_count: number;
  has_catalog: boolean;
  size_mb?: number;
}

interface TableContents {
  name: string;
  partitions: number;
  files: number;
  size_mb: number;
}

interface BackupContents {
  date: string;
  tables: TableContents[];
  catalog_present: boolean;
}

// ---------------------------------------------------------------
// Health badge
// ---------------------------------------------------------------

function HealthBadge({
  status,
}: {
  status: BackupHealth["status"];
}) {
  const config = {
    healthy: {
      dot: "bg-emerald-500",
      text: "text-emerald-700 dark:text-emerald-400",
      bg: "bg-emerald-50 dark:bg-emerald-900/20",
      label: "Healthy",
    },
    stale: {
      dot: "bg-amber-500",
      text: "text-amber-700 dark:text-amber-400",
      bg: "bg-amber-50 dark:bg-amber-900/20",
      label: "Stale",
    },
    critical: {
      dot: "bg-red-500",
      text: "text-red-700 dark:text-red-400",
      bg: "bg-red-50 dark:bg-red-900/20",
      label: "Critical",
    },
    missing: {
      dot: "bg-gray-400",
      text: "text-gray-600 dark:text-gray-400",
      bg: "bg-gray-50 dark:bg-gray-800",
      label: "No Backups",
    },
  };
  const c = config[status];
  return (
    <span
      className={`inline-flex items-center
        gap-1.5 px-2.5 py-1 rounded-full
        text-xs font-medium ${c.bg} ${c.text}`}
    >
      <span
        className={`h-2 w-2 rounded-full ${c.dot}`}
      />
      {c.label}
    </span>
  );
}

// ---------------------------------------------------------------
// Main component
// ---------------------------------------------------------------

export function BackupHealthPanel() {
  const [health, setHealth] =
    useState<BackupHealth | null>(null);
  const [backups, setBackups] = useState<
    BackupEntry[]
  >([]);
  const [expanded, setExpanded] = useState<
    string | null
  >(null);
  const [contents, setContents] = useState<
    Record<string, BackupContents>
  >({});
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [hRes, bRes] = await Promise.all([
        apiFetch(
          `${API_URL}/admin/backups/health`,
        ),
        apiFetch(`${API_URL}/admin/backups`),
      ]);
      if (hRes.ok) {
        setHealth(await hRes.json());
      }
      if (bRes.ok) {
        const d = await bRes.json();
        setBackups(d.backups ?? []);
      }
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const toggleContents = useCallback(
    async (dt: string) => {
      if (expanded === dt) {
        setExpanded(null);
        return;
      }
      setExpanded(dt);
      if (contents[dt]) return;
      try {
        const res = await apiFetch(
          `${API_URL}/admin/backups/${dt}/contents`,
        );
        if (res.ok) {
          const data = await res.json();
          setContents((prev) => ({
            ...prev,
            [dt]: data,
          }));
        }
      } catch {
        /* ignore */
      }
    },
    [expanded, contents],
  );

  if (loading) {
    return (
      <div
        className="animate-pulse rounded-xl
          bg-gray-100 dark:bg-gray-800/50
          h-40"
      />
    );
  }

  const fmtAge = (h: number) => {
    if (h < 1) return "< 1h ago";
    if (h < 24) return `${Math.round(h)}h ago`;
    const d = Math.round(h / 24);
    return `${d}d ago`;
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div
        className="flex items-center
          justify-between"
      >
        <div className="flex items-center gap-3">
          <h3
            className="text-sm font-semibold
              text-gray-700 dark:text-gray-200"
          >
            Backup Health
          </h3>
          {health && (
            <HealthBadge status={health.status} />
          )}
        </div>
        <button
          onClick={fetchData}
          className="text-xs px-3 py-1.5
            rounded-lg border border-gray-300
            dark:border-gray-600
            text-gray-700 dark:text-gray-300
            hover:bg-gray-50
            dark:hover:bg-gray-700
            transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Summary cards */}
      {health && health.status !== "missing" && (
        <div
          className="grid grid-cols-2
            sm:grid-cols-4 gap-3"
        >
          <div
            className="rounded-lg border
              border-gray-200 dark:border-gray-700
              p-3"
          >
            <p
              className="text-[10px] uppercase
                tracking-wider text-gray-400"
            >
              Latest Backup
            </p>
            <p
              className="text-sm font-semibold
                text-gray-800 dark:text-gray-100
                mt-0.5"
            >
              {health.latest_date}
            </p>
            {health.age_hours != null && (
              <p
                className="text-[10px]
                  text-gray-400 mt-0.5"
              >
                {fmtAge(health.age_hours)}
              </p>
            )}
          </div>
          <div
            className="rounded-lg border
              border-gray-200 dark:border-gray-700
              p-3"
          >
            <p
              className="text-[10px] uppercase
                tracking-wider text-gray-400"
            >
              Backups Stored
            </p>
            <p
              className="text-sm font-semibold
                text-gray-800 dark:text-gray-100
                mt-0.5"
            >
              {health.backup_count}
            </p>
          </div>
          <div
            className="rounded-lg border
              border-gray-200 dark:border-gray-700
              p-3"
          >
            <p
              className="text-[10px] uppercase
                tracking-wider text-gray-400"
            >
              Size
            </p>
            <p
              className="text-sm font-semibold
                text-gray-800 dark:text-gray-100
                mt-0.5"
            >
              {health.size_mb != null
                ? health.size_mb >= 1024
                  ? `${(health.size_mb / 1024).toFixed(1)} GB`
                  : `${health.size_mb.toFixed(0)} MB`
                : "\u2014"}
            </p>
          </div>
          <div
            className="rounded-lg border
              border-gray-200 dark:border-gray-700
              p-3"
          >
            <p
              className="text-[10px] uppercase
                tracking-wider text-gray-400"
            >
              Catalog
            </p>
            <p
              className="text-sm font-semibold
                mt-0.5"
            >
              {health.has_catalog ? (
                <span
                  className="text-emerald-600
                    dark:text-emerald-400"
                >
                  Included
                </span>
              ) : (
                <span
                  className="text-red-600
                    dark:text-red-400"
                >
                  Missing
                </span>
              )}
            </p>
          </div>
        </div>
      )}

      {health?.status === "missing" && (
        <p
          className="text-sm text-gray-400
            dark:text-gray-500 text-center py-6"
        >
          No backups found. Run maintenance to
          create the first backup.
        </p>
      )}

      {/* Backup list */}
      {backups.length > 0 && (
        <div
          className="rounded-lg border
            border-gray-200 dark:border-gray-700
            overflow-hidden"
        >
          <table className="w-full text-sm">
            <thead>
              <tr
                className="bg-gray-50
                  dark:bg-gray-800/50
                  text-left"
              >
                <th
                  className="px-3 py-2
                    text-xs font-medium
                    text-gray-500
                    dark:text-gray-400"
                >
                  Date
                </th>
                <th
                  className="px-3 py-2
                    text-xs font-medium
                    text-gray-500
                    dark:text-gray-400
                    text-right"
                >
                  Size
                </th>
                <th
                  className="px-3 py-2
                    text-xs font-medium
                    text-gray-500
                    dark:text-gray-400"
                >
                  Age
                </th>
                <th
                  className="px-3 py-2
                    text-xs font-medium
                    text-gray-500
                    dark:text-gray-400"
                >
                  Catalog
                </th>
                <th
                  className="px-3 py-2
                    text-xs font-medium
                    text-gray-500
                    dark:text-gray-400"
                >
                  Details
                </th>
              </tr>
            </thead>
            <tbody
              className="divide-y divide-gray-100
                dark:divide-gray-800"
            >
              {backups.map((b) => (
                <tr key={b.date}>
                  <td
                    className="px-3 py-2
                      font-medium text-gray-800
                      dark:text-gray-100"
                  >
                    {b.date}
                  </td>
                  <td
                    className="px-3 py-2
                      text-right text-gray-600
                      dark:text-gray-300
                      tabular-nums"
                  >
                    {b.size_mb >= 1024
                      ? `${(b.size_mb / 1024).toFixed(1)} GB`
                      : `${b.size_mb.toFixed(0)} MB`}
                  </td>
                  <td
                    className="px-3 py-2
                      text-gray-500
                      dark:text-gray-400"
                  >
                    {fmtAge(b.age_hours)}
                  </td>
                  <td className="px-3 py-2">
                    {b.has_catalog ? (
                      <span
                        className="text-emerald-600
                          dark:text-emerald-400
                          text-xs"
                      >
                        Yes
                      </span>
                    ) : (
                      <span
                        className="text-red-500
                          text-xs"
                      >
                        No
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() =>
                        toggleContents(b.date)
                      }
                      className="text-indigo-600
                        dark:text-indigo-400
                        text-xs hover:underline"
                    >
                      {expanded === b.date
                        ? "Hide"
                        : "Browse"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Expanded contents */}
          {expanded && contents[expanded] && (
            <div
              className="border-t border-gray-200
                dark:border-gray-700 bg-gray-50
                dark:bg-gray-800/30 px-4 py-3"
            >
              <p
                className="text-xs font-medium
                  text-gray-500 dark:text-gray-400
                  mb-2"
              >
                Tables in backup-{expanded}
                {contents[expanded]
                  .catalog_present && (
                  <span
                    className="ml-2
                      text-emerald-600
                      dark:text-emerald-400"
                  >
                    catalog.db present
                  </span>
                )}
              </p>
              <div
                className="grid grid-cols-1
                  sm:grid-cols-2 lg:grid-cols-3
                  gap-2"
              >
                {contents[expanded].tables.map(
                  (t) => (
                    <div
                      key={t.name}
                      className="rounded-md border
                      border-gray-200
                      dark:border-gray-700
                      bg-white dark:bg-gray-800
                      px-3 py-2"
                    >
                      <p
                        className="text-xs
                        font-mono font-medium
                        text-gray-700
                        dark:text-gray-200"
                      >
                        {t.name}
                      </p>
                      <p
                        className="text-[10px]
                        text-gray-400 mt-0.5"
                      >
                        {t.partitions} partitions
                        {" \u00B7 "}
                        {t.files} files
                        {" \u00B7 "}
                        {t.size_mb.toFixed(1)} MB
                      </p>
                    </div>
                  ),
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
