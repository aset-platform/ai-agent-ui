import type { NextConfig } from "next";
import bundleAnalyzer from "@next/bundle-analyzer";

const withBundleAnalyzer = bundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
});

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
  async rewrites() {
    const backend =
      process.env.BACKEND_URL || "http://localhost:8181";
    return [
      { source: "/v1/:path*", destination: `${backend}/v1/:path*` },
    ];
  },
};

export default withBundleAnalyzer(nextConfig);
