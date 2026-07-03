import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { usePagination } from "../usePagination";

describe("usePagination", () => {
  it("slices rows to the page size, defaulting to page 1", () => {
    const rows = Array.from({ length: 40 }, (_, i) => i);
    const { result } = renderHook(() => usePagination(rows, 15));
    expect(result.current.page).toBe(1);
    expect(result.current.totalPages).toBe(3);
    expect(result.current.pageRows).toEqual(
      Array.from({ length: 15 }, (_, i) => i),
    );
  });

  it("advances to the requested page", () => {
    const rows = Array.from({ length: 40 }, (_, i) => i);
    const { result } = renderHook(() => usePagination(rows, 15));
    act(() => result.current.setPage(2));
    expect(result.current.page).toBe(2);
    expect(result.current.pageRows).toEqual(
      Array.from({ length: 15 }, (_, i) => i + 15),
    );
  });

  it("resets to page 1 when the page size changes", () => {
    const rows = Array.from({ length: 40 }, (_, i) => i);
    const { result } = renderHook(() => usePagination(rows, 15));
    act(() => result.current.setPage(3));
    expect(result.current.page).toBe(3);
    act(() => result.current.setPageSize(25));
    expect(result.current.page).toBe(1);
    expect(result.current.pageSize).toBe(25);
    expect(result.current.totalPages).toBe(2);
  });

  it("clamps the current page when the row list shrinks below it", () => {
    const rows = Array.from({ length: 40 }, (_, i) => i);
    const { result, rerender } = renderHook(
      ({ rows: r }) => usePagination(r, 15),
      { initialProps: { rows } },
    );
    act(() => result.current.setPage(3));
    expect(result.current.page).toBe(3);
    rerender({ rows: rows.slice(0, 5) });
    expect(result.current.page).toBe(1);
    expect(result.current.pageRows).toEqual([0, 1, 2, 3, 4]);
  });

  it("returns a single page (page 1 of 1) for an empty row list", () => {
    const { result } = renderHook(() => usePagination([], 15));
    expect(result.current.totalPages).toBe(1);
    expect(result.current.page).toBe(1);
    expect(result.current.pageRows).toEqual([]);
  });
});
