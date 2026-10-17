/**
 * Forward events to the backend: POST {BACKEND_URL}/webhooks/{photon|elevenlabs}.
 * Header X-Webhook-Secret. Body: {eventId, jobId, type, ...payload}. The backend removes
 * duplicates by eventId, so a stable eventId makes a retry (or two sources) safe.
 */
import type { Config } from "./config.js";

export type EventType = "sms_in" | "call_started" | "transcript_line" | "call_ended";

export interface BackendEvent {
  eventId: string;
  jobId: string;
  type: EventType;
  [key: string]: unknown;
}

export type Forwarder = (provider: "photon" | "elevenlabs", event: BackendEvent) => Promise<boolean>;

export function makeForwarder(config: Config, fetchImpl: typeof fetch = fetch, retries = 2): Forwarder {
  return async (provider, event) => {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const resp = await fetchImpl(`${config.backendUrl}/webhooks/${provider}`, {
          method: "POST",
          headers: { "content-type": "application/json", "X-Webhook-Secret": config.webhookSecret },
          body: JSON.stringify(event),
          signal: AbortSignal.timeout(config.httpTimeoutMs),
        });
        if (resp.ok) return true;
        if (resp.status < 500) {
          console.warn(`[forward] ${event.type} ${event.eventId}: HTTP ${resp.status}`);
          return false;
        }
      } catch (err) {
        console.warn(`[forward] ${event.type} ${event.eventId}: ${(err as Error).name}`);
      }
      await new Promise((r) => setTimeout(r, 300 * 2 ** attempt));
    }
    return false;
  };
}
