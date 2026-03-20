"use client";

interface WidgetSkeletonProps {
  className?: string;
}

export function WidgetSkeleton({
  className,
}: WidgetSkeletonProps) {
  return (
    <div
      className={`relative overflow-hidden rounded-xl bg-gray-200 dark:bg-gray-800 animate-pulse ${className ?? "h-48"}`}
    >
      <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent dark:via-white/5" />
    </div>
  );
}
