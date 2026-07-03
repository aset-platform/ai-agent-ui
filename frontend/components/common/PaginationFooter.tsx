"use client";

const PAGE_SIZE_OPTIONS = [15, 25, 50, 100];

interface Props {
  page: number;
  totalPages: number;
  pageSize: number;
  totalRows: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  /** Disambiguates data-testids when multiple paginated tables
   * render on the same page (e.g. Performance's strategy
   * comparison table + trade log table). */
  testIdPrefix: string;
}

export function PaginationFooter({
  page,
  totalPages,
  pageSize,
  totalRows,
  onPageChange,
  onPageSizeChange,
  testIdPrefix,
}: Props) {
  return (
    <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400 px-1">
      <div className="flex items-center gap-2">
        <span>
          {totalRows} row{totalRows !== 1 ? "s" : ""}
        </span>
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          data-testid={`${testIdPrefix}-page-size`}
          className="rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-1.5 py-0.5 text-xs"
        >
          {PAGE_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>{n}/page</option>
          ))}
        </select>
      </div>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          data-testid={`${testIdPrefix}-prev`}
          className="rounded px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Prev
        </button>
        <span data-testid={`${testIdPrefix}-page-indicator`}>
          {page} / {totalPages}
        </span>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          data-testid={`${testIdPrefix}-next`}
          className="rounded px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Next
        </button>
      </div>
    </div>
  );
}
