/**
 * Configuration from the repo-root `.env` (then `services/photon/.env`, then the process env).
 * Secret values are never printed. `describeConfig()` reports only "set" / "not set".
 */
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const SERVICE_DIR = resolve(here, "..");
export const REPO_DIR = resolve(SERVICE_DIR, "..", "..");

export function loadEnvFiles(): void {
  for (const file of [resolve(REPO_DIR, ".env"), resolve(SERVICE_DIR, ".env")]) {
    // process.loadEnvFile never overrides a variable that is already set.
    if (existsSync(file)) process.loadEnvFile(file);
  }
}

function str(name: string, fallback = ""): string {
  const value = process.env[name];
  return value === undefined || value.trim() === "" ? fallback : value.trim();
}

function num(name: string, fallback: number): number {
  const value = Number(str(name));
  return Number.isFinite(value) && str(name) !== "" ? value : fallback;
}

export type InboundMode = "stream" | "poll" | "webhook" | "off";
const INBOUND_MODES: readonly InboundMode[] = ["stream", "poll", "webhook", "off"];

export interface Config {
  port: number;
  backendUrl: string;
  webhookSecret: string;
  serviceToken: string; // optional: required on POST /sms and /calls when set
  /** Phones that this service may text or call. Everything else gets 403. */
  allowedPhones: Set<string>;
  photon: {
    projectId: string; // beta project ID, pho_prj_...
    apiKey: string; // beta project API key, pho_sk_...
    phoneNumber: string; // our SMS number (E.164); texts are sent from it
    baseUrl: string;
    webhookSecret: string; // webhook destination signing secret, whsec_... (only for webhook mode)
    inbound: InboundMode;
    pollMs: number;
  };
  elevenlabs: {
    apiKey: string;
    agentId: string;
    agentPhoneNumberId: string;
    baseUrl: string;
    webhookSecret: string; // post-call webhook HMAC secret
    toolSecret: string; // X-Tool-Secret header that the live-transcript webhook tool sends
    ringingTimeoutS: number;
    pollMs: number;
    callMaxS: number;
  };
  httpTimeoutMs: number;
}

export function normalizePhone(phone: string): string {
  const digits = phone.replace(/[^\d+]/g, "");
  if (digits.startsWith("+")) return digits;
  if (digits.length === 10) return `+1${digits}`;
  return digits ? `+${digits}` : "";
}

export function readConfig(): Config {
  const allowed = [str("DEMO_DISTRIBUTOR_PHONE"), str("OWNER_PHONE"), ...str("PHOTON_ALLOWED_PHONES").split(",")]
    .map((p) => normalizePhone(p))
    .filter(Boolean);
  const inbound = str("PHOTON_INBOUND_MODE", "stream") as InboundMode;
  return {
    port: num("PHOTON_SERVICE_PORT", 8787),
    backendUrl: str("BACKEND_URL", "http://localhost:8000").replace(/\/$/, ""),
    webhookSecret: str("WEBHOOK_SECRET", "dev-webhook"),
    serviceToken: str("PHOTON_SERVICE_TOKEN"),
    allowedPhones: new Set(allowed),
    photon: {
      projectId: str("PHOTON_PROJECT_ID"),
      // The beta credential is a project API key. PHOTON_PROJECT_SECRET is accepted as a fallback alias.
      apiKey: str("PHOTON_API_KEY", str("PHOTON_PROJECT_SECRET")),
      phoneNumber: normalizePhone(str("PHOTON_PHONE_NUMBER")),
      baseUrl: str("PHOTON_API_BASE_URL", "https://api.photon.codes").replace(/\/$/, ""),
      webhookSecret: str("PHOTON_WEBHOOK_SECRET"),
      inbound: INBOUND_MODES.includes(inbound) ? inbound : "stream",
      pollMs: num("PHOTON_POLL_MS", 3000),
    },
    elevenlabs: {
      apiKey: str("ELEVENLABS_API_KEY"),
      agentId: str("ELEVENLABS_AGENT_ID"),
      agentPhoneNumberId: str("ELEVENLABS_AGENT_PHONE_NUMBER_ID"),
      baseUrl: str("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").replace(/\/$/, ""),
      webhookSecret: str("ELEVENLABS_WEBHOOK_SECRET"),
      toolSecret: str("ELEVENLABS_TOOL_SECRET"),
      ringingTimeoutS: num("CALL_RINGING_TIMEOUT_S", 20),
      pollMs: num("ELEVENLABS_POLL_MS", 1500),
      callMaxS: num("CALL_MAX_S", 300),
    },
    httpTimeoutMs: num("PHOTON_HTTP_TIMEOUT_MS", 10_000),
  };
}

/** Safe summary for logs: never the values of secrets or phone numbers. */
export function describeConfig(c: Config): Record<string, string | number> {
  const set = (v: string) => (v ? "set" : "not set");
  return {
    port: c.port,
    backendUrl: c.backendUrl,
    allowedPhones: c.allowedPhones.size,
    photonProject: set(c.photon.projectId && c.photon.apiKey),
    photonNumber: c.photon.phoneNumber ? `***${c.photon.phoneNumber.slice(-4)}` : "not set",
    photonInbound: c.photon.inbound,
    photonWebhookSecret: set(c.photon.webhookSecret),
    elevenlabs: set(c.elevenlabs.apiKey && c.elevenlabs.agentId && c.elevenlabs.agentPhoneNumberId),
    elevenlabsWebhookSecret: set(c.elevenlabs.webhookSecret),
    elevenlabsToolSecret: set(c.elevenlabs.toolSecret),
    serviceToken: set(c.serviceToken),
  };
}
