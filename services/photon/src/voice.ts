/**
 * Outbound calls: ElevenLabs Agents over the Photon SIP trunk.
 *
 * Confirmed by the ElevenLabs API reference:
 * - POST /v1/convai/sip-trunk/outbound-call  (header xi-api-key)
 *   body {agent_id, agent_phone_number_id, to_number, conversation_initiation_client_data:
 *   {dynamic_variables}, telephony_call_config: {ringing_timeout_secs}}
 *   -> {success, message, conversation_id|null, sip_call_id|null}
 * - GET /v1/convai/conversations/{id} -> {status: initiated|in-progress|processing|done|failed,
 *   transcript: [{role: agent|user, message, time_in_call_secs}], metadata}
 * - Post-call webhook `post_call_transcription`: header `ElevenLabs-Signature: t=<ts>,v0=<hex>`,
 *   HMAC-SHA256 over `<ts>.<rawBody>`.
 *
 * Photon's part (confirmed, dashboard only): the Photon voice profile is the SIP trunk that
 * ElevenLabs dials out through (sip.photon.codes:5061, TLS, digest auth). Photon has no SDK
 * call control ("coming soon").
 *
 * Unconfirmed: whether the conversation transcript fills in DURING the call. The poller emits
 * new transcript turns as they appear; if they only appear at the end, `call_ended` carries the
 * full transcript and the backend replays it (spec §10.2 fallback). The real-time monitor
 * WebSocket (/v1/convai/conversations/{id}/monitor) is Enterprise-only.
 * TODO(enterprise): use the monitor WebSocket for true live lines when the plan allows it.
 */
import { createHmac, timingSafeEqual } from "node:crypto";
import type { BackendEvent, Forwarder } from "./backend.js";
import type { Config } from "./config.js";

export interface OpeningContext {
  bestTotal: number;
  items: { id?: string; name: string; quantity: number; unit?: string }[];
  businessName?: string;
}

export interface TranscriptTurn {
  role: string;
  message: string | null;
}

export interface Conversation {
  status: string;
  transcript: TranscriptTurn[];
  dynamicVariables: Record<string, unknown>;
}

export interface VoiceApi {
  readonly mode: "elevenlabs" | "dry-run";
  startCall(to: string, variables: Record<string, string | number>): Promise<{ conversationId: string }>;
  getConversation(conversationId: string): Promise<Conversation>;
}

export function dynamicVariables(jobId: string, ctx: OpeningContext): Record<string, string | number> {
  const items = ctx.items.map((i) => `${i.quantity} ${i.unit ?? ""} ${i.name}`.replace(/\s+/g, " ").trim());
  return {
    job_id: jobId,
    business_name: ctx.businessName ?? "Hearthloaf Bakeries",
    best_total: `$${ctx.bestTotal.toFixed(2)}`,
    item_count: ctx.items.length,
    items_list: items.join("; "),
  };
}

export class ElevenLabsVoice implements VoiceApi {
  readonly mode = "elevenlabs" as const;
  constructor(private config: Config, private fetchImpl: typeof fetch = fetch) {}

  private async call(path: string, init: RequestInit = {}, timeoutMs = this.config.httpTimeoutMs): Promise<unknown> {
    const resp = await this.fetchImpl(`${this.config.elevenlabs.baseUrl}${path}`, {
      ...init,
      headers: { "content-type": "application/json", "xi-api-key": this.config.elevenlabs.apiKey },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!resp.ok) {
      // The error body has no secrets; it names the failed field or the SIP reason.
      const detail = (await resp.text().catch(() => "")).slice(0, 300);
      throw new Error(`elevenlabs ${path}: HTTP ${resp.status} ${detail}`);
    }
    return resp.json();
  }

  async startCall(to: string, variables: Record<string, string | number>) {
    const e = this.config.elevenlabs;
    const data = (await this.call("/v1/convai/sip-trunk/outbound-call", {
      method: "POST",
      body: JSON.stringify({
        agent_id: e.agentId,
        agent_phone_number_id: e.agentPhoneNumberId,
        to_number: to,
        conversation_initiation_client_data: { dynamic_variables: variables },
        telephony_call_config: { ringing_timeout_secs: e.ringingTimeoutS },
      }),
      // The outbound-call request returns only after the SIP call is set up, which can include
      // the whole ringing time. A 10 s limit aborted the first real test call.
    }, (e.ringingTimeoutS + 25) * 1000)) as { success?: boolean; message?: string; conversation_id?: string | null };
    if (!data.success || !data.conversation_id) {
      throw new Error(`outbound call not started: ${data.message ?? "no conversation_id"}`);
    }
    return { conversationId: data.conversation_id };
  }

  async getConversation(conversationId: string): Promise<Conversation> {
    const data = (await this.call(`/v1/convai/conversations/${encodeURIComponent(conversationId)}`)) as {
      status?: string;
      transcript?: TranscriptTurn[];
      conversation_initiation_client_data?: { dynamic_variables?: Record<string, unknown> };
    };
    return {
      status: data.status ?? "unknown",
      transcript: data.transcript ?? [],
      dynamicVariables: data.conversation_initiation_client_data?.dynamic_variables ?? {},
    };
  }
}

/** No ElevenLabs keys: accept the request and report a call that is never answered. */
export class DryRunVoice implements VoiceApi {
  readonly mode = "dry-run" as const;
  private n = 0;
  async startCall() {
    this.n += 1;
    console.log("[call dry-run] no ElevenLabs credentials; no call is placed");
    return { conversationId: `dry_conv_${this.n}` };
  }
  async getConversation(): Promise<Conversation> {
    return { status: "failed", transcript: [], dynamicVariables: {} };
  }
}

export function createVoice(config: Config, fetchImpl: typeof fetch = fetch): VoiceApi {
  const e = config.elevenlabs;
  return e.apiKey && e.agentId && e.agentPhoneNumberId ? new ElevenLabsVoice(config, fetchImpl) : new DryRunVoice();
}

const speakerOf = (role: string) => (role === "user" ? "supplier" : "agent");

export function transcriptForBackend(turns: TranscriptTurn[]) {
  return turns
    .map((t, seq) => ({ seq, speaker: speakerOf(t.role), text: (t.message ?? "").trim() }))
    .filter((t) => t.text);
}

/**
 * Poll one conversation and forward call_started / transcript_line / call_ended.
 * Event ids are stable per conversation (`<id>:started`, `<id>:t<seq>`, `<id>:ended`), so the
 * post-call webhook and the poller can both report the end and the backend keeps one.
 */
export class CallTracker {
  private sentLines = new Set<number>();
  private started = false;
  private done = false;

  constructor(
    private voice: VoiceApi,
    private forward: Forwarder,
    readonly jobId: string,
    readonly conversationId: string,
    private opts: { pollMs: number; callMaxS: number },
  ) {}

  get finished(): boolean {
    return this.done;
  }

  private event(type: BackendEvent["type"], suffix: string, payload: Record<string, unknown> = {}): BackendEvent {
    return { eventId: `${this.conversationId}:${suffix}`, jobId: this.jobId, type, callId: this.conversationId, ...payload };
  }

  async observe(conv: Conversation): Promise<void> {
    if (this.done) return;
    const turns = transcriptForBackend(conv.transcript);
    if (!this.started && (conv.status === "in-progress" || turns.length > 0 || conv.status === "done")) {
      this.started = true;
      await this.forward("elevenlabs", this.event("call_started", "started"));
    }
    if (conv.status === "in-progress") {
      for (const t of turns) {
        if (this.sentLines.has(t.seq)) continue;
        this.sentLines.add(t.seq);
        await this.forward("elevenlabs", this.event("transcript_line", `t${t.seq}`, t));
      }
    }
    if (conv.status === "done" || conv.status === "failed") await this.end(conv.status, turns);
  }

  async end(status: string, turns: ReturnType<typeof transcriptForBackend>): Promise<void> {
    if (this.done) return;
    this.done = true;
    const outcome = status === "failed" ? (this.started ? "failed" : "no_answer") : "completed";
    await this.forward("elevenlabs", this.event("call_ended", "ended", { outcome, transcript: turns, final: true }));
  }

  async run(): Promise<void> {
    const until = Date.now() + this.opts.callMaxS * 1000;
    while (!this.done && Date.now() < until) {
      try {
        await this.observe(await this.voice.getConversation(this.conversationId));
      } catch (err) {
        console.warn(`[call] poll failed: ${(err as Error).message}`);
      }
      if (!this.done) await new Promise((r) => setTimeout(r, this.opts.pollMs));
    }
    if (!this.done) await this.end("failed", []);
  }
}

/** Verify `ElevenLabs-Signature: t=<ts>,v0=<hex>` (HMAC-SHA256 of `<ts>.<rawBody>`). */
export function verifyElevenLabsSignature(rawBody: string, header: string | undefined, secret: string,
                                          nowS = Math.floor(Date.now() / 1000), toleranceS = 1800): boolean {
  if (!header || !secret) return false;
  const parts = Object.fromEntries(header.split(",").map((p) => p.trim().split("=", 2) as [string, string]));
  const ts = Number(parts.t);
  const sig = parts.v0;
  if (!Number.isFinite(ts) || !sig || Math.abs(nowS - ts) > toleranceS) return false;
  const expected = createHmac("sha256", secret).update(`${ts}.${rawBody}`).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(sig);
  return a.length === b.length && timingSafeEqual(a, b);
}
