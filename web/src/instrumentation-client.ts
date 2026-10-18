import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
// The backend is another origin, so list it: then a click and the job it starts share one trace.
const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

if (dsn) {
  Sentry.init({
    dsn,
    integrations: [Sentry.replayIntegration()],
    tracesSampleRate: 1.0, // every demo run shows in Sentry
    // Only the backend: other local services (the Agentation toolbar) reject the trace headers.
    tracePropagationTargets: [apiBase],
    replaysSessionSampleRate: 1.0,
    replaysOnErrorSampleRate: 1.0,
  });
}

export function onRouterTransitionStart(href: string, navigationType: string) {
  if (dsn) Sentry.captureRouterTransitionStart(href, navigationType);
}
