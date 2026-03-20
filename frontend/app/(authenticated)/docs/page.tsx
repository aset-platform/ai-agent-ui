"use client";

import { useState, useMemo } from "react";
import { IFrameView } from "@/components/IFrameView";
import { DOCS_URL } from "@/lib/config";
import { useTheme } from "@/hooks/useTheme";

export default function DocsPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const theme = useTheme();

  const src = useMemo(() => {
    const sep = DOCS_URL.includes("?") ? "&" : "?";
    return `${DOCS_URL}${sep}theme=${theme.resolvedTheme}`;
  }, [theme.resolvedTheme]);

  return (
    <IFrameView
      src={src}
      title="Documentation"
      loading={loading}
      error={error}
      onLoad={() => setLoading(false)}
      onError={() => { setLoading(false); setError(true); }}
    />
  );
}
