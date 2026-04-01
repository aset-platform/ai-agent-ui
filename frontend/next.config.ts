import type { NextConfig } from "next";
import bundleAnalyzer from "@next/bundle-analyzer";

const withBundleAnalyzer = bundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
});

const nextConfig: NextConfig = {
  output: "standalone",
  devIndicators: false,
  serverExternalPackages: [
    "lightningcss",
    "lightningcss-linux-arm64-musl",
    "lightningcss-linux-arm64-gnu",
    "lightningcss-darwin-arm64",
    "lightningcss-darwin-x64",
  ],
};

export default withBundleAnalyzer(nextConfig);
