/**
 * HTTP routes.
 *   POST /sms    {jobId, to, text}                                   -> {messageId}
 *   POST /calls  {jobId, to, openingContext: {bestTotal, items, businessName}} -> {callId, status}
 *   GET  /health
 *   POST /photon/webhook            (Photon beta webhook destination; signature-verified)
 *   POST /elevenlabs/post-call      (ElevenLabs post-call webhook; HMAC-verified)
 *   POST /elevenlabs/live-line      (the agent's `log_live_turn` webhook tool; X-Tool-Secret)
 *
 * Guardrail: /sms and /calls accept only numbers in the allowlist (DEMO_DISTRIBUTOR_PHONE,
 * OWNER_PHONE, PHOTON_ALLOWED_PHONES). Any other number gets 403 and nothing is sent.
 */
import { randomUUID, timingSafeEqual } from "node:crypto";
import { Hono } from "hono";
import type { Forwarder } from "./backend.js";
import { type Config, normalizePhone } from "./config.js";
import type { InboundText, Messenger } from "./sms.js";
import {
  CallTracker,
  dynamicVariables,
  type OpeningContext,
  transcriptForBackend,
  verifyElevenLabsSignature,
  type VoiceApi,
} from "./voice.js";

export interface Deps {
  config: Config;
  messenger: Messenger;
  voice: VoiceApi;
  forward: Forwarder;
}

export interface ServiceState {
  /** phone -> the last job that texted it (inbound texts carry no job id). */
  jobByPhone: Map<string, string>;
  calls: Map<string, CallTracker>;
}

export function createApp(deps: Deps, state: ServiceState = { jobByPhone: new Map(), calls: new Map() }) {
  const { config, messenger, voice, forward } = deps;
  const app = new Hono();

  const authorized = (header: string | undefined) => !config.serviceToken || header === config.serviceToken;
  const allowed = (phone: string) => config.allowedPhones.has(phone);

  const onInboundText = (msg: InboundText) => {
    const jobId = state.jobByPhone.get(msg.from);
    if (!jobId) {
      console.warn("[sms] inbound text from a number with no open job; ignored");
      return;
    }
    void forward("photon", {
      eventId: `sms:${msg.id || randomUUID()}`,
      jobId,
      type: "sms_in",
      text: msg.text,
      messageId: msg.id,
    });
  };

  app.get("/health", (c) =>
    c.json({
      ok: true,
      sms: messenger.mode,
      voice: voice.mode,
      allowedPhones: config.allowedPhones.size,
      activeCalls: [...state.calls.values()].filter((t) => !t.finished).length,
    }),
  );

  app.post("/sms", async (c) => {
    if (!authorized(c.req.header("X-Service-Token"))) return c.json({ error: "unauthorized" }, 401);
    const raw = (await c.req.json().catch(() => ({}))) as unknown;
    const body = (raw && typeof raw === "object" ? raw : {}) as { jobId?: unknown; to?: unknown; text?: unknown };
    // A JSON number or object here made body.text.slice() throw (500): require strings.
    if (typeof body.jobId !== "string" || typeof body.to !== "string" || typeof body.text !== "string") {
      return c.json({ error: "jobId, to and text are required strings" }, 400);
    }
    const to = normalizePhone(body.to);
    if (!body.jobId || !to || !body.text) return c.json({ error: "jobId, to and text are required" }, 400);
    if (!allowed(to)) return c.json({ error: "number not in the team allowlist" }, 403);
    try {
      const { messageId } = await messenger.send(to, body.text.slice(0, 1000));
      state.jobByPhone.set(to, body.jobId);
      return c.json({ messageId, mode: messenger.mode });
    } catch (err) {
      console.error(`[sms] send failed: ${(err as Error).message}`);
      return c.json({ error: "send failed" }, 502);
    }
  });

  app.post("/calls", async (c) => {
    if (!authorized(c.req.header("X-Service-Token"))) return c.json({ error: "unauthorized" }, 401);
    const body = ((await c.req.json().catch(() => ({}))) ?? {}) as {
      jobId?: string;
      to?: string;
      openingContext?: OpeningContext;
    };
    const to = typeof body.to === "string" ? normalizePhone(body.to) : "";
    const ctx = body.openingContext;
    if (typeof body.jobId !== "string" || !body.jobId || !to || !ctx || typeof ctx.bestTotal !== "number" || !Array.isArray(ctx.items)) {
      return c.json({ error: "jobId, to and openingContext {bestTotal, items} are required" }, 400);
    }
    if (!allowed(to)) return c.json({ error: "number not in the team allowlist" }, 403);
    try {
      const variables = dynamicVariables(body.jobId, ctx);
      // Read the greeting while the phone rings, so the live transcript can show it on answer.
      const greeting = voice.firstMessage ? voice.firstMessage(variables) : Promise.resolve(null);
      const { conversationId } = await voice.startCall(to, variables);
      state.jobByPhone.set(to, body.jobId);
      const tracker = new CallTracker(voice, forward, body.jobId, conversationId, {
        pollMs: config.elevenlabs.pollMs,
        callMaxS: config.elevenlabs.callMaxS,
        greeting: await greeting,
      });
      state.calls.set(conversationId, tracker);
      void tracker.run();
      return c.json({ callId: conversationId, status: "dialing", mode: voice.mode });
    } catch (err) {
      console.error(`[call] start failed: ${(err as Error).message}`);
      return c.json({ error: "call failed to start" }, 502);
    }
  });

  app.post("/photon/webhook", async (c) => {
    if (!messenger.webhook) return c.json({ error: "Photon webhook is not available (dry-run)" }, 404);
    return messenger.webhook(c.req.raw, onInboundText);
  });

  app.post("/elevenlabs/post-call", async (c) => {
    const raw = await c.req.text();
    if (!verifyElevenLabsSignature(raw, c.req.header("ElevenLabs-Signature"), config.elevenlabs.webhookSecret)) {
      return c.json({ error: "bad signature" }, 401);
    }
    const payload = JSON.parse(raw) as {
      type?: string;
      data?: {
        conversation_id?: string;
        status?: string;
        transcript?: { role: string; message: string | null }[];
        conversation_initiation_client_data?: { dynamic_variables?: Record<string, unknown> };
      };
    };
    const data = payload.data ?? {};
    const conversationId = data.conversation_id ?? "";
    const tracker = state.calls.get(conversationId);
    const jobId = tracker?.jobId ?? String(data.conversation_initiation_client_data?.dynamic_variables?.job_id ?? "");
    if (!conversationId || !jobId) return c.json({ ignored: true });
    if (payload.type === "post_call_transcription") {
      const turns = transcriptForBackend(data.transcript ?? []);
      if (tracker) await tracker.end("done", turns);
      else {
        await forward("elevenlabs", {
          eventId: `${conversationId}:ended`, jobId, type: "call_ended", callId: conversationId,
          outcome: "completed", transcript: turns, final: true,
        });
      }
    } else if (payload.type === "call_initiation_failure") {
      // TODO(confirm): the docs do not list this payload's data fields; the id fields above are assumed.
      if (tracker) await tracker.end("failed", []);
    }
    return c.json({ ok: true });
  });

  // Live transcript: ElevenLabs fills the conversation transcript only after the call (the
  // monitor WebSocket is Enterprise-only), so the agent calls this tool on each of its turns.
  app.post("/elevenlabs/live-line", async (c) => {
    const secret = config.elevenlabs.toolSecret;
    if (!secret) return c.json({ error: "ELEVENLABS_TOOL_SECRET is not set" }, 404);
    const given = Buffer.from(c.req.header("X-Tool-Secret") ?? "");
    const want = Buffer.from(secret);
    if (given.length !== want.length || !timingSafeEqual(given, want)) return c.json({ error: "unauthorized" }, 401);
    const body = (await c.req.json().catch(() => ({}))) as {
      conversation_id?: string;
      supplier_said?: string;
      agent_reply?: string;
    };
    const tracker = state.calls.get(body.conversation_id ?? "");
    if (!tracker) {
      console.warn("[call] live line for a conversation this service did not start; ignored");
      return c.json({ ok: true });
    }
    // The tool runs in `immediate` mode: the agent waits for this reply before it speaks, so answer
    // at once and forward the lines in the background. (`async` mode made the agent say each line
    // twice: its "task completed" result started a second LLM turn.) The duplicate check in
    // liveTurn runs before its first await, so it still applies.
    tracker.liveTurn(String(body.supplier_said ?? ""), String(body.agent_reply ?? "")).catch((err) => {
      console.warn(`[call] live line not forwarded: ${(err as Error).message}`);
    });
    // The agent reads this reply, so keep it short.
    return c.json({ ok: true });
  });

  return { app, state, onInboundText };
}
