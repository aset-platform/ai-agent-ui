export default function AnalyticsLoading() {
  return (
    <div className="animate-pulse space-y-6 p-6">
      {/* Tab bar skeleton */}
      <div className="flex gap-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="h-9 w-28 bg-gray-200 dark:bg-gray-700 rounded-lg"
          />
        ))}
      </div>
      {/* Chart skeleton */}
      <div className="h-80 bg-gray-200 dark:bg-gray-700 rounded-xl" />
      {/* Controls skeleton */}
      <div className="flex gap-4">
        <div className="h-10 w-40 bg-gray-200 dark:bg-gray-700 rounded-lg" />
        <div className="h-10 w-32 bg-gray-200 dark:bg-gray-700 rounded-lg" />
      </div>
    </div>
  );
}
