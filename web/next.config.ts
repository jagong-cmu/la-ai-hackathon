import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";

const nextConfig: NextConfig = {
  reactStrictMode: true,
};

// Sentry is a no-op unless NEXT_PUBLIC_SENTRY_DSN is set. Source map upload
// runs only when SENTRY_AUTH_TOKEN (plus SENTRY_ORG / SENTRY_PROJECT) is set.
export default process.env.NEXT_PUBLIC_SENTRY_DSN
  ? withSentryConfig(nextConfig, {
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      authToken: process.env.SENTRY_AUTH_TOKEN,
      silent: !process.env.CI,
    })
  : nextConfig;
