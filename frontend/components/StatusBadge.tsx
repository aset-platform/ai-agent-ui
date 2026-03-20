/**
 * Animated pulsing status badge shown while the assistant is processing.
 */

export function StatusBadge({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-3" data-testid="status-badge">
      <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse shrink-0" />
      <span className="text-sm text-gray-500 dark:text-gray-400 italic">{text}</span>
    </div>
  );
}
