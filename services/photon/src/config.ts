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

export interface Config {
  port: number;
  backendUrl: string;
  webhookSecret: string;
  serviceToken: string; // optional: required on POST /sms and /calls when set
  /** Phones that this service may text or call. Everything else gets 403. */
  allowedPhones: Set<string>;
  photon: {
    projectId: string;
    projectSecret: string;
    webhookSecret: string; // Spectrum webhook signing secret (only for webhook mode)
    inbound: "stream" | "webhook" | "off";
  };
  elevenlabs: {
    apiKey: string;
    agentId: string;
    agentPhoneNumberId: string;
    baseUrl: string;
    webhookSecret: string; // post-call webhook HMAC secret
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
  const inbound = str("PHOTON_INBOUND_MODE", "stream");
  return {
    port: num("PHOTON_SERVICE_PORT", 8787),
    backendUrl: str("BACKEND_URL", "http://localhost:8000").replace(/\/$/, ""),
    webhookSecret: str("WEBHOOK_SECRET", "dev-webhook"),
    serviceToken: str("PHOTON_SERVICE_TOKEN"),
    allowedPhones: new Set(allowed),
    photon: {
      projectId: str("PHOTON_PROJECT_ID", str("SPECTRUM_PROJECT_ID")),
      // The docs name this value the project *secret*. PHOTON_API_KEY is accepted as an alias.
      projectSecret: str("PHOTON_PROJECT_SECRET", str("PHOTON_API_KEY", str("SPECTRUM_PROJECT_SECRET"))),
      webhookSecret: str("PHOTON_WEBHOOK_SECRET", str("SPECTRUM_WEBHOOK_SECRET")),
      inbound: inbound === "webhook" || inbound === "off" ? inbound : "stream",
    },
    elevenlabs: {
      apiKey: str("ELEVENLABS_API_KEY"),
      agentId: str("ELEVENLABS_AGENT_ID"),
      agentPhoneNumberId: str("ELEVENLABS_AGENT_PHONE_NUMBER_ID"),
      baseUrl: str("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").replace(/\/$/, ""),
      webhookSecret: str("ELEVENLABS_WEBHOOK_SECRET"),
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
    photonProject: set(c.photon.projectId && c.photon.projectSecret),
    photonInbound: c.photon.inbound,
    elevenlabs: set(c.elevenlabs.apiKey && c.elevenlabs.agentId && c.elevenlabs.agentPhoneNumberId),
    elevenlabsWebhookSecret: set(c.elevenlabs.webhookSecret),
    serviceToken: set(c.serviceToken),
  };
}
