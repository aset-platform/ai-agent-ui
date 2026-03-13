/**
 * Iframe wrapper with loading spinner and error fallback overlay.
 *
 * Used to embed the Dash dashboard, MkDocs documentation, and admin pages
 * within the chat shell without navigating away.
 */

interface IFrameViewProps {
  src: string;
  title: string;
  loading: boolean;
  error: boolean;
  onLoad: () => void;
  onError: () => void;
}

export function IFrameView({ src, title, loading, error, onLoad, onError }: IFrameViewProps) {
  return (
    <div className="flex-1 relative overflow-hidden">
      {loading && !error && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm text-gray-500">Loading...</span>
          </div>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
          <div className="flex flex-col items-center gap-3 text-center px-6">
            <p className="text-gray-600 font-medium">Could not load content.</p>
            <p className="text-sm text-gray-400">Make sure the service is running.</p>
            <a
              href={src}
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 underline text-sm hover:text-indigo-800"
            >
              Open in new tab ↗
            </a>
          </div>
        </div>
      )}
      <iframe
        src={src}
        className="w-full h-full border-0"
        title={title}
        onLoad={onLoad}
        onError={onError}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-top-navigation"
        referrerPolicy="no-referrer"
      />
    </div>
  );
}
