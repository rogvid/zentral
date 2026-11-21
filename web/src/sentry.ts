// Error monitoring in the browser, off unless the build was given a DSN.
//
// Tickets, names and messages are on nearly every screen, so only what describes
// the code leaves the page: error types, stack frames and the release. No replay,
// no tracing, no breadcrumbs (clicks and URLs) and no error messages, which can
// quote what someone typed. The server does the same: zentral/observability.py.
import * as Sentry from "@sentry/react";

type Options = NonNullable<Parameters<typeof Sentry.init>[0]>;

// `Required`: a category a future SDK adds must be decided here, because the
// SDK's default for one left out is to collect it.
const dataCollection = {
  userInfo: false,
  cookies: false,
  httpHeaders: false,
  httpBodies: [],
  urlQueryParams: false,
  graphQL: { document: false, variables: false },
  genAI: { inputs: false, outputs: false },
  mcp: { inputs: false, outputs: false },
  databaseQueryData: false,
  queues: false,
  stackFrameVariables: false,
  frameContextLines: 0,
} satisfies Required<NonNullable<Options["dataCollection"]>>;

const UNUSED_INTEGRATIONS = new Set(["Breadcrumbs", "BrowserSession", "HttpContext"]);

export function scrubEvent(event: Sentry.ErrorEvent): Sentry.ErrorEvent {
  delete event.request;
  // Sentry fills in a city from the sender's address unless the event already
  // has a location, whatever the IP settings say. An empty one stores none.
  event.user = { geo: {} };
  delete event.breadcrumbs;
  delete event.message;
  delete event.logentry;
  delete event.extra;
  delete event.transaction;
  delete event.server_name;
  for (const entry of event.exception?.values ?? []) delete entry.value;
  return event;
}

export function startSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return;
  Sentry.init({
    dsn,
    release: import.meta.env.VITE_SENTRY_RELEASE || undefined,
    environment: import.meta.env.MODE,
    dataCollection,
    maxBreadcrumbs: 0,
    integrations: (defaults) => defaults.filter((i) => !UNUSED_INTEGRATIONS.has(i.name)),
    beforeSend: scrubEvent,
  });
}
