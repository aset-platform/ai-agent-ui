export default function AdminLoading() {
  return (
    <div className="animate-pulse space-y-6 p-6">
      {/* Header skeleton */}
      <div className="h-8 w-48 bg-gray-200 dark:bg-gray-700 rounded" />
      {/* Table skeleton */}
      <div className="space-y-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <div
            key={i}
            className="h-12 bg-gray-200 dark:bg-gray-700 rounded-lg"
          />
        ))}
      </div>
    </div>
  );
}
