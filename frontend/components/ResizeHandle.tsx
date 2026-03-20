/**
 * Vertical drag handle for resizing the chat panel.
 *
 * Renders a thin bar on the left edge of the panel. On hover it
 * highlights; on drag it triggers the ``onMouseDown`` handler
 * from ``useResizePanel``.
 */

interface ResizeHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
}

export function ResizeHandle({ onMouseDown }: ResizeHandleProps) {
  return (
    <div
      role="separator"
      aria-label="Resize chat panel"
      onMouseDown={onMouseDown}
      className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize group hover:bg-violet-500/20 transition-colors z-10"
    >
      <div className="absolute left-0.5 top-1/2 -translate-y-1/2 w-0.5 h-8 bg-gray-300 dark:bg-gray-600 rounded-full group-hover:bg-violet-500 transition-colors" />
    </div>
  );
}
