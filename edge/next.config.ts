import type { NextConfig } from "next";

/**
 * Thin edge only — no rewrite bypass around OAuth.
 * Authenticated traffic is proxied in app/proxy/[...path]/route.ts
 * so Cloud Run IAM ID tokens can be attached.
 */
const nextConfig: NextConfig = {
  poweredByHeader: false,
};

export default nextConfig;
