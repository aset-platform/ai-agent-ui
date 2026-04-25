import type { NextConfig } from "next";
import bundleAnalyzer from "@next/bundle-analyzer";

const withBundleAnalyzer = bundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
});

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
  // Partial Prerendering — Next.js 16 renamed
  // `experimental.ppr` to top-level `cacheComponents`.
  //
  // Currently FALSE (scaffolded, not active) because
  // enabling it in dev surfaces "new Date() inside a
  // Client Component without a Suspense boundary"
  // errors on /dashboard, /analytics, /admin — those
  // routes are still client-only and don't have the
  // streaming boundaries PPR needs. Flipping to true
  // is part of ASETPLTFRM-334 phase A's RSC
  // migration, where the dashboard becomes a Server
  // Component and dynamic islands get explicit
  // <Suspense> wrappers.
  //
  // Keeping the line here so the migration is one
  // value-flip rather than a multi-file config search.
  // (ASETPLTFRM-334 phase F)
  cacheComponents: false,
  async rewrites() {
    const backend =
      process.env.BACKEND_URL || "http://localhost:8181";
    return [
      { source: "/v1/:path*", destination: `${backend}/v1/:path*` },
    ];
  },
};

export default withBundleAnalyzer(nextConfig);
