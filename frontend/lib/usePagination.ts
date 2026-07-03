"use client";

import { useMemo, useState } from "react";

export interface UsePaginationResult<T> {
  page: number;
  setPage: (page: number) => void;
  pageSize: number;
  setPageSize: (size: number) => void;
  totalPages: number;
  pageRows: T[];
}

/** Client-side pagination over an already-filtered/sorted row
 * list. Changing pageSize resets to page 1; page is clamped to
 * totalPages so a shrinking row list (e.g. a filter change) never
 * strands the user on a page beyond the new last page. */
export function usePagination<T>(
  rows: T[],
  initialPageSize = 15,
): UsePaginationResult<T> {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(initialPageSize);

  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const clampedPage = Math.min(page, totalPages);
  const pageRows = useMemo(
    () => rows.slice(
      (clampedPage - 1) * pageSize, clampedPage * pageSize,
    ),
    [rows, clampedPage, pageSize],
  );

  function setPageSize(size: number) {
    setPageSizeState(size);
    setPage(1);
  }

  return {
    page: clampedPage, setPage, pageSize, setPageSize, totalPages, pageRows,
  };
}
