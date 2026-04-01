export default function LoginLoading() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="animate-pulse w-full max-w-md space-y-4 p-8">
        {/* Logo skeleton */}
        <div className="h-10 w-32 mx-auto bg-gray-200 dark:bg-gray-700 rounded" />
        {/* Form fields skeleton */}
        <div className="h-10 bg-gray-200 dark:bg-gray-700 rounded-lg" />
        <div className="h-10 bg-gray-200 dark:bg-gray-700 rounded-lg" />
        {/* Button skeleton */}
        <div className="h-11 bg-gray-200 dark:bg-gray-700 rounded-lg" />
      </div>
    </div>
  );
}
