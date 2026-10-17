/**
 * Text messages (SMS) through the Photon 2.0 beta API (`@photon-ai/api` 0.5.0).
 *
 * Confirmed by the beta docs (photon.codes/docs/beta/...) and by https://api.photon.codes/openapi.json:
 * - Auth: `Authorization: Bearer <project API key>` (`pho_sk_...`), base URL `https://api.photon.codes`.
 * - Send: a message needs a Space and a sender User. The flow is
 *     POST /v1/projects/{id}/users   {platform:"sms", handle:<our number>}   -> agent User
 *     POST /v1/projects/{id}/users   {platform:"sms", handle:<team phone>}   -> human User
 *     POST /v1/projects/{id}/spaces  {platform:"sms", participantIds:[agent, human]} + Idempotency-Key
 *     POST /v1/projects/{id}/messages {spaceId, senderId:agent, content:{type:"text", format:"plain", text}}
 *       + Idempotency-Key -> 202 (accepted for dispatch; delivery is not confirmed)
 * - Receive (three documented paths; all give the same Message object):
 *     stream:  Socket.IO at wss://api.photon.codes, path /v1/projects/{id}/events/ws, event `delivery`
 *              with `message.received` events (no public URL needed)
 *     poll:    GET /v1/projects/{id}/messages?direction=received&platform=sms (no public URL needed)
 *     webhook: a webhook destination POSTs the same event JSON to a public URL (needs a tunnel)
 *
 * NOT confirmed: the beta docs do not describe the webhook signature headers. The signing secret has
 * the `whsec_` prefix, so `verifyPhotonWebhook` uses the Standard Webhooks scheme
 * (`webhook-id`, `webhook-timestamp`, `webhook-signature: v1,<base64 HMAC-SHA256>`). See the README.
 *
 * Without credentials (key, project ID, or our SMS number), `DryRunMessenger` is used: it logs a
 * redacted line and sends nothing.
 */
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { Photon } from "@photon-ai/api";
import type { Config } from "./config.js";
import { normalizePhone } from "./config.js";

export interface InboundText {
  id: string;
  from: string; // E.164 phone, or "" when the event gives no phone
  text: string;
}

export interface Messenger {
  readonly mode: "photon" | "dry-run";
  send(to: string, text: string): Promise<{ messageId: string }>;
  /** Start delivering inbound texts (stream or poll mode). Returns a stop function. */
  listen(onText: (msg: InboundText) => void): Promise<() => Promise<void>>;
  /** Webhook mode: verify and handle one inbound webhook request. */
  webhook?(request: Request, onText: (msg: InboundText) => void): Promise<Response>;
}

export const redact = (phone: string) => (phone.length > 4 ? `***${phone.slice(-4)}` : "***");

export class DryRunMessenger implements Messenger {
  readonly mode = "dry-run" as const;
  private n = 0;
  async send(to: string, text: string) {
    this.n += 1;
    console.log(`[sms dry-run] to ${redact(to)}: ${text.length} chars (no Photon credentials; nothing sent)`);
    return { messageId: `dry_${this.n}` };
  }
  async listen() {
    return async () => {};
  }
}

// ---------- inbound parsing (shared by stream, poll and webhook) ----------

type Json = Record<string, unknown>;
const obj = (v: unknown): Json | undefined => (v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : undefined);
const s = (v: unknown): string => (typeof v === "string" ? v : "");

/**
 * Convert one Photon Message (from listMessages, or `event.data.object`) to an InboundText.
 * Returns null for anything that is not a received plain SMS text to our number.
 */
export function messageToInbound(message: unknown, ourNumber: string): InboundText | null {
  const m = obj(message);
  if (!m || s(m.platform) !== "sms" || s(m.status) !== "received") return null;
  const content = obj(m.content);
  if (!content || s(content.type) !== "text") return null;
  const sentBy = obj(m.sentBy);
  if (!sentBy || s(sentBy.kind) === "agent") return null;
  const receivedBy = obj(m.receivedBy);
  const to = normalizePhone(s(receivedBy?.handle));
  if (to && ourNumber && to !== ourNumber) return null; // a text to another line of this project
  return { id: s(m.id), from: normalizePhone(s(sentBy.handle)), text: s(content.text) };
}

/** Convert one public event (`message.received`) to an InboundText. */
export function eventToInbound(event: unknown, ourNumber: string): InboundText | null {
  const e = obj(event);
  if (!e || s(e.type) !== "message.received") return null;
  return messageToInbound(obj(e.data)?.object, ourNumber);
}

// ---------- webhook signature (Standard Webhooks scheme; NOT confirmed by the beta docs) ----------

export const WEBHOOK_TOLERANCE_S = 300;

export function signPhotonWebhook(secret: string, id: string, timestamp: string, body: string): string {
  const key = Buffer.from(secret.replace(/^whsec_/, ""), "base64");
  return `v1,${createHmac("sha256", key).update(`${id}.${timestamp}.${body}`).digest("base64")}`;
}

export function verifyPhotonWebhook(
  body: string,
  headers: { id?: string | null; timestamp?: string | null; signature?: string | null },
  secret: string,
  nowS = Math.floor(Date.now() / 1000),
): boolean {
  const { id, timestamp, signature } = headers;
  if (!secret || !id || !timestamp || !signature) return false;
  const ts = Number(timestamp);
  if (!Number.isInteger(ts) || Math.abs(nowS - ts) > WEBHOOK_TOLERANCE_S) return false;
  const expected = Buffer.from(signPhotonWebhook(secret, id, timestamp, body));
  return signature.split(" ").some((candidate) => {
    const got = Buffer.from(candidate.trim());
    return got.length === expected.length && timingSafeEqual(got, expected);
  });
}

// ---------- Socket.IO stream (injectable for tests) ----------

/** The small part of a socket.io-client Socket that this service uses. */
export interface EventSocket {
  on(event: string, listener: (...args: any[]) => void): unknown;
  timeout(ms: number): { emit(event: string, payload: unknown, ack: (err: unknown, result: any) => void): unknown };
  connect(): unknown;
  disconnect(): unknown;
}
export type SocketFactory = (url: string, options: Record<string, unknown>) => EventSocket | Promise<EventSocket>;

const defaultSocketFactory: SocketFactory = async (url, options) => {
  const { io } = await import("socket.io-client");
  return io(url, options) as unknown as EventSocket;
};

export interface MessengerDeps {
  fetch?: typeof fetch;
  socketFactory?: SocketFactory;
  retry?: false;
}

// ---------- the Photon beta messenger ----------

export class PhotonMessenger implements Messenger {
  readonly mode = "photon" as const;
  private readonly photon: Photon;
  private agentUserId: Promise<string> | null = null;
  private readonly spaceByPhone = new Map<string, Promise<string>>();
  private readonly seen = new Set<string>();

  constructor(
    private readonly config: Config,
    private readonly deps: MessengerDeps = {},
  ) {
    const { apiKey, baseUrl } = config.photon;
    this.photon = new Photon({
      baseUrl,
      headers: { Authorization: `Bearer ${apiKey}` },
      timeoutMs: config.httpTimeoutMs,
      ...(deps.fetch ? { fetch: deps.fetch } : {}),
      ...(deps.retry === false ? { retry: false as const } : {}),
    });
  }

  private get projectId() {
    return this.config.photon.projectId;
  }

  private async userId(handle: string): Promise<string> {
    const user = await this.photon.projects.createOrGetUser({
      path: { id: this.projectId },
      body: { platform: "sms", handle },
    });
    return s(user.id);
  }

  private agent(): Promise<string> {
    if (!this.agentUserId) {
      this.agentUserId = this.userId(this.config.photon.phoneNumber).catch((err) => {
        this.agentUserId = null;
        throw err;
      });
    }
    return this.agentUserId;
  }

  private space(to: string): Promise<string> {
    let space: Promise<string> | undefined = this.spaceByPhone.get(to);
    if (!space) {
      space = (async () => {
        const [agentId, humanId] = await Promise.all([this.agent(), this.userId(to)]);
        const created = await this.photon.projects.createOrGetSpace({
          path: { id: this.projectId },
          // SMS Spaces converge by participants, so this key is stable for the pair.
          headers: { "idempotency-key": `sms-space:${agentId}:${humanId}` },
          body: { platform: "sms", participantIds: [agentId, humanId] },
        });
        return s(created.id);
      })().catch((err) => {
        this.spaceByPhone.delete(to);
        throw err;
      });
      this.spaceByPhone.set(to, space);
    }
    return space;
  }

  async send(to: string, text: string) {
    const [spaceId, senderId] = await Promise.all([this.space(to), this.agent()]);
    const sent = await this.photon.projects.sendMessage({
      path: { id: this.projectId },
      headers: { "idempotency-key": `sms-out:${randomUUID()}` },
      body: { spaceId, senderId, content: { type: "text", format: "plain", text } },
    });
    return { messageId: s(sent.id) };
  }

  /** Deliver each inbound message one time only (stream, poll and webhook can overlap). */
  private deliver(msg: InboundText | null, onText: (msg: InboundText) => void) {
    if (!msg || !msg.id || this.seen.has(msg.id)) return;
    this.seen.add(msg.id);
    if (this.seen.size > 5000) this.seen.delete(this.seen.values().next().value as string);
    onText(msg);
  }

  async listen(onText: (msg: InboundText) => void) {
    if (this.config.photon.inbound === "poll") return this.poll(onText);
    if (this.config.photon.inbound === "stream") return this.stream(onText);
    return async () => {};
  }

  /** Poll mode: GET /v1/projects/{id}/messages (the walkthrough's documented method). */
  async poll(onText: (msg: InboundText) => void, signal?: AbortSignal) {
    let since = Date.now() - 5_000;
    let stopped = false;
    let timer: NodeJS.Timeout | undefined;
    const tick = async () => {
      try {
        let pageToken: string | undefined;
        do {
          const page = await this.photon.projects.listMessages({
            path: { id: this.projectId },
            query: {
              createdSince: new Date(since).toISOString(),
              direction: "received",
              order: "asc",
              pageSize: 100,
              platform: "sms",
              ...(pageToken ? { pageToken } : {}),
            },
          });
          for (const m of page.items) {
            const created = Date.parse(s((m as Json).createdAt));
            // Keep a 30 s overlap for eventual consistency; `seen` removes the duplicates.
            if (Number.isFinite(created)) since = Math.max(since, created - 30_000);
            this.deliver(messageToInbound(m, this.config.photon.phoneNumber), onText);
          }
          pageToken = page.nextPageToken ?? undefined;
        } while (pageToken && !stopped);
      } catch (err) {
        console.warn(`[sms] poll failed: ${(err as Error).name}`);
      }
      if (!stopped && !signal?.aborted) timer = setTimeout(tick, this.config.photon.pollMs);
    };
    void tick();
    return async () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }

  /** Stream mode: Socket.IO event delivery with an in-memory replay cursor. */
  async stream(onText: (msg: InboundText) => void) {
    const { baseUrl, apiKey } = this.config.photon;
    const factory = this.deps.socketFactory ?? defaultSocketFactory;
    const socket = await factory(baseUrl, {
      path: `/v1/projects/${this.projectId}/events/ws`,
      addTrailingSlash: false,
      transports: ["websocket"],
      extraHeaders: { Authorization: `Bearer ${apiKey}` },
      autoConnect: false,
    });
    let cursor = 0;
    socket.on("connect", () => {
      socket.timeout(10_000).emit("subscribe", { startSeq: cursor }, (error: unknown, result: any) => {
        if (error) console.warn("[sms] stream subscribe timed out");
        else if (!result?.ok) console.warn(`[sms] stream subscribe refused: ${s(result?.error?.code)}`);
        else console.log("[sms] stream subscribed");
      });
    });
    socket.on("delivery", (event: unknown, meta: { seq?: number } | undefined) => {
      try {
        this.deliver(eventToInbound(event, this.config.photon.phoneNumber), onText);
      } catch (err) {
        console.warn(`[sms] stream event failed: ${(err as Error).message}`);
      }
      if (typeof meta?.seq === "number" && meta.seq > cursor) cursor = meta.seq;
    });
    socket.on("delivery_error", (e: { code?: string }) => console.warn(`[sms] stream delivery_error: ${s(e?.code)}`));
    socket.on("connect_error", (e: Error) => console.warn(`[sms] stream connect_error: ${e?.message ?? ""}`));
    socket.connect();
    return async () => {
      socket.disconnect();
    };
  }

  async webhook(request: Request, onText: (msg: InboundText) => void): Promise<Response> {
    const body = await request.text();
    const ok = verifyPhotonWebhook(
      body,
      {
        id: request.headers.get("webhook-id"),
        timestamp: request.headers.get("webhook-timestamp"),
        signature: request.headers.get("webhook-signature"),
      },
      this.config.photon.webhookSecret,
    );
    if (!ok) {
      console.warn("[sms] webhook rejected: bad or missing signature");
      return Response.json({ error: "bad signature" }, { status: 401 });
    }
    let event: unknown;
    try {
      event = JSON.parse(body);
    } catch {
      return Response.json({ error: "bad json" }, { status: 400 });
    }
    this.deliver(eventToInbound(event, this.config.photon.phoneNumber), onText);
    return Response.json({ ok: true });
  }
}

export async function createMessenger(config: Config, deps: MessengerDeps = {}): Promise<Messenger> {
  const { projectId, apiKey, phoneNumber } = config.photon;
  if (!projectId || !apiKey || !phoneNumber) return new DryRunMessenger();
  if (!projectId.startsWith("pho_prj_")) {
    console.warn("[sms] PHOTON_PROJECT_ID does not start with pho_prj_; the beta API will reject it");
  }
  return new PhotonMessenger(config, deps);
}
