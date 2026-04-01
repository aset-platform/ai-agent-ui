export default function DashboardLoading() {
  return (
    <div className="animate-pulse space-y-6 p-6">
      {/* Hero section skeleton */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-24 bg-gray-200 dark:bg-gray-700 rounded-xl"
          />
        ))}
      </div>
      {/* Watchlist skeleton */}
      <div className="h-64 bg-gray-200 dark:bg-gray-700 rounded-xl" />
      {/* Widgets skeleton */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="h-48 bg-gray-200 dark:bg-gray-700 rounded-xl" />
        <div className="h-48 bg-gray-200 dark:bg-gray-700 rounded-xl" />
      </div>
    </div>
  );
}
