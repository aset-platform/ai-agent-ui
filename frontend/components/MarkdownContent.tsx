/**
 * Renders assistant markdown responses with custom Tailwind-styled components.
 *
 * Internal links (pointing to the Dash dashboard or MkDocs site) are rendered
 * as buttons that switch the in-app view rather than opening a new tab.
 */

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DOCS_URL } from "@/lib/config";

function preprocessContent(content: string): string {
  // Rewrite legacy Dash chart URLs to native Next.js routes
  content = content.replace(
    /\S+\/charts\/analysis\/([A-Z0-9._-]+)_analysis\.html/g,
    (_, ticker) => `[View ${ticker} Analysis →](/analytics/analysis?ticker=${ticker})`
  );
  content = content.replace(
    /\S+\/charts\/forecasts\/([A-Z0-9._-]+)_forecast\.html/g,
    (_, ticker) => `[View ${ticker} Forecast →](/analytics/analysis?ticker=${ticker})`
  );
  content = content.replace(/\S+\/data\/(raw|processed|forecasts|cache|metadata)\/\S+/g, "");

  return content;
}

interface MarkdownContentProps {
  content: string;
  onInternalLink: (href: string) => void;
}

export function MarkdownContent({ content, onInternalLink }: MarkdownContentProps) {
  const docsBase = DOCS_URL;

  // Fix #4: memoize preprocessing — avoids re-running regex on every streaming chunk
  const processedContent = useMemo(() => preprocessContent(content), [content]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h1 className="text-lg font-bold mt-3 mb-1 first:mt-0">{children}</h1>,
        h2: ({ children }) => <h2 className="text-base font-semibold mt-3 mb-1 first:mt-0">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-semibold mt-2 mb-1 first:mt-0">{children}</h3>,
        p:  ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
        ul: ({ children }) => <ul className="list-disc pl-5 mb-2 space-y-0.5">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 space-y-0.5">{children}</ol>,
        li: ({ children }) => <li className="leading-relaxed">{children}</li>,
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        em:     ({ children }) => <em className="italic">{children}</em>,
        hr: () => <hr className="border-gray-200 dark:border-gray-600 my-3" />,
        blockquote: ({ children }) => (
          <blockquote className="border-l-4 border-indigo-300 dark:border-indigo-600 pl-3 italic text-gray-500 dark:text-gray-400 my-2">{children}</blockquote>
        ),
        a: ({ href, children }) => {
          const isInternal = href && (href.startsWith("/") || href.startsWith(docsBase));
          if (isInternal) {
            return (
              <button
                onClick={() => onInternalLink(href!)}
                className="text-indigo-600 dark:text-indigo-400 underline hover:text-indigo-800 dark:hover:text-indigo-300 cursor-pointer text-left"
              >
                {children}
              </button>
            );
          }
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-indigo-600 dark:text-indigo-400 underline hover:text-indigo-800 dark:hover:text-indigo-300">
              {children}
            </a>
          );
        },
        pre: ({ children }) => (
          <pre className="bg-gray-900 dark:bg-gray-950 text-gray-100 rounded-lg px-4 py-3 overflow-x-auto text-xs font-mono my-2">
            {children}
          </pre>
        ),
        code: ({ className, children }) =>
          className ? (
            <code className="font-mono">{children}</code>
          ) : (
            <code className="bg-gray-100 dark:bg-gray-700 text-indigo-700 dark:text-indigo-300 rounded px-1 py-0.5 text-[0.83em] font-mono">
              {children}
            </code>
          ),
        table: ({ children }) => (
          <div className="overflow-x-auto my-2">
            <table className="min-w-full text-xs border-collapse">{children}</table>
          </div>
        ),
        thead: ({ children }) => <thead className="bg-gray-50 dark:bg-gray-700">{children}</thead>,
        th: ({ children }) => (
          <th className="border border-gray-200 dark:border-gray-600 px-3 py-1.5 font-semibold text-left text-gray-700 dark:text-gray-300">{children}</th>
        ),
        td: ({ children }) => (
          <td className="border border-gray-200 dark:border-gray-600 px-3 py-1.5 text-gray-700 dark:text-gray-300">{children}</td>
        ),
        tr: ({ children }) => <tr className="even:bg-gray-50 dark:even:bg-gray-700/50">{children}</tr>,
      }}
    >
      {processedContent}
    </ReactMarkdown>
  );
}
