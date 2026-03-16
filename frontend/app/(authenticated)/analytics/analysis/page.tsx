"use client";

import { useState, useMemo } from "react";
import { IFrameView } from "@/components/IFrameView";
import { DASHBOARD_URL } from "@/lib/config";
import { getAccessToken } from "@/lib/auth";
import { useTheme } from "@/hooks/useTheme";

export default function AnalysisPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const theme = useTheme();

  const src = useMemo(() => {
    const base = `${DASHBOARD_URL}/analysis`;
    const token = getAccessToken();
    const sep = base.includes("?") ? "&" : "?";
    const params = token
      ? `token=${encodeURIComponent(token)}&theme=${theme.resolvedTheme}`
      : `theme=${theme.resolvedTheme}`;
    return `${base}${sep}${params}`;
  }, [theme.resolvedTheme]);

  return (
    <IFrameView
      src={src}
      title="Analysis"
      loading={loading}
      error={error}
      onLoad={() => setLoading(false)}
      onError={() => {
        setLoading(false);
        setError(true);
      }}
    />
  );
}
