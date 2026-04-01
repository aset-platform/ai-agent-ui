"use client";

interface WidgetErrorProps {
  message: string;
  "data-testid"?: string;
}

export function WidgetError({
  message,
  "data-testid": testId,
}: WidgetErrorProps) {
  return (
    <div
      data-testid={testId}
      className="
        rounded-xl p-6
        bg-red-50 dark:bg-red-900/20
        border border-red-200 dark:border-red-800
        flex items-start gap-3
      "
    >
      {/* Warning icon */}
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="currentColor"
        className="
          w-6 h-6 shrink-0 mt-0.5
          text-red-500 dark:text-red-400
        "
      >
        <path
          fillRule="evenodd"
          d="M9.401 3.003c1.155-2 4.043-2 5.197
             0l7.355 12.748c1.154 2-.29 4.499-2.599
             4.499H4.645c-2.309
             0-3.752-2.5-2.598-4.5L9.4 3.004zM12
             8.25a.75.75 0 0 1 .75.75v3.75a.75.75
             0 0 1-1.5 0V9a.75.75 0 0 1
             .75-.75zm0 8.25a.75.75 0 1 0
             0-1.5.75.75 0 0 0 0 1.5z"
          clipRule="evenodd"
        />
      </svg>

      <div>
        <p className="text-sm font-medium text-red-800 dark:text-red-200">
          Could not load
        </p>
        <p className="text-sm text-red-600 dark:text-red-400 mt-0.5">
          {message}
        </p>
      </div>
    </div>
  );
}
