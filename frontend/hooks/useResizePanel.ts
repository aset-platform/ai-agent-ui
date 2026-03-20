"use client";
/**
 * Hook for drag-to-resize panel width.
 *
 * Returns the current width and a mousedown handler to attach
 * to a resize handle element.
 */

import { useState, useCallback, useRef } from "react";

export function useResizePanel(
  minWidth: number,
  maxWidth: number,
  defaultWidth: number,
) {
  const [width, setWidth] = useState(defaultWidth);
  const isDragging = useRef(false);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;

      const onMove = (ev: MouseEvent) => {
        if (!isDragging.current) return;
        const newW = window.innerWidth - ev.clientX;
        setWidth(
          Math.min(maxWidth, Math.max(minWidth, newW)),
        );
      };

      const onUp = () => {
        isDragging.current = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [minWidth, maxWidth],
  );

  return { width, onMouseDown };
}
