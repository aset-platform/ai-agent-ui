/**
 * Centralised CSV download utility.
 *
 * Converts an array of objects into a CSV blob and
 * triggers a browser download. Works with any flat
 * data — use `columnMap` to pick/rename/reorder fields.
 */

export interface CsvColumn<T> {
  /** Object key to extract the value from. */
  key: keyof T & string;
  /** Header label in the CSV. Defaults to `key`. */
  header?: string;
  /** Optional formatter — receives raw value,
   *  returns string for the CSV cell. */
  format?: (value: T[keyof T], row: T) => string;
}

/**
 * Download an array of objects as a CSV file.
 *
 * @param rows     - Data to export (filtered/sorted).
 * @param columns  - Column definitions (pick, rename,
 *                   reorder, format).
 * @param filename - Output filename (without extension).
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function downloadCsv<T extends Record<string, any>>(
  rows: T[],
  columns: CsvColumn<T>[],
  filename: string,
): void {
  if (rows.length === 0) return;

  const escape = (v: string): string => {
    if (
      v.includes(",") ||
      v.includes('"') ||
      v.includes("\n")
    ) {
      return `"${v.replace(/"/g, '""')}"`;
    }
    return v;
  };

  const headers = columns.map(
    (c) => escape(c.header ?? c.key),
  );

  const body = rows.map((row) =>
    columns
      .map((c) => {
        const raw = row[c.key];
        if (raw == null) return "";
        const str = c.format
          ? c.format(raw, row)
          : String(raw);
        return escape(str);
      })
      .join(","),
  );

  const csv = [headers.join(","), ...body].join(
    "\n",
  );
  const blob = new Blob([csv], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
