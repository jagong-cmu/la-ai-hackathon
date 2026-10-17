import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import type { BackendEvent } from "./backend.js";
import { makeForwarder } from "./backend.js";
import type { Config } from "./config.js";
import { createApp } from "./server.js";
import { DryRunMessenger } from "./sms.js";
import { type Conversation, verifyElevenLabsSignature, type VoiceApi } from "./voice.js";

const TEAM = "+15551234567";

function config(): Config {
  return {
    port: 0,
    backendUrl: "http://backend.test",
    webhookSecret: "wh",
    serviceToken: "",
    allowedPhones: new Set([TEAM]),
    photon: { projectId: "", projectSecret: "", webhookSecret: "", inbound: "off" },
    elevenlabs: {
      apiKey: "", agentId: "", agentPhoneNumberId: "", baseUrl: "http://el.test", webhookSecret: "el-secret",
      ringingTimeoutS: 20, pollMs: 5, callMaxS: 2,
    },
    httpTimeoutMs: 1000,
  };
}

class ScriptedVoice implements VoiceApi {
  readonly mode = "elevenlabs" as const;
  variables: Record<string, string | number> = {};
  constructor(private frames: Conversation[]) {}
  async startCall(_to: string, variables: Record<string, string | number>) {
    this.variables = variables;
    return { conversationId: "conv_1" };
  }
  async getConversation() {
    return this.frames.length > 1 ? this.frames.shift()! : this.frames[0]!;
  }
}

function setup(voice: VoiceApi) {
  const events: [string, BackendEvent][] = [];
  const forward = async (provider: "photon" | "elevenlabs", e: BackendEvent) => {
    events.push([provider, e]);
    return true;
  };
  const { app, onInboundText } = createApp({ config: config(), messenger: new DryRunMessenger(), voice, forward });
  return { app, events, onInboundText };
}

const post = (app: { request: (p: string, i: RequestInit) => Response | Promise<Response> }, path: string, body: unknown) =>
  app.request(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

test("sms only goes to allowlisted team phones; replies are routed to the job", async () => {
  const { app, events, onInboundText } = setup(new ScriptedVoice([]));
  assert.equal((await post(app, "/sms", { jobId: "j1", to: "+15550000000", text: "hi" })).status, 403);
  const ok = await post(app, "/sms", { jobId: "j1", to: "(555) 123-4567", text: "hi" });
  assert.equal(ok.status, 200);
  onInboundText({ id: "m9", from: TEAM, text: "Gloves $250" });
  assert.deepEqual(events, [["photon", { eventId: "sms:m9", jobId: "j1", type: "sms_in", text: "Gloves $250", messageId: "m9" }]]);
});

test("a call forwards call_started, live transcript lines and one call_ended", async () => {
  const frames: Conversation[] = [
    { status: "initiated", transcript: [], dynamicVariables: {} },
    { status: "in-progress", transcript: [{ role: "agent", message: "Hi, this is an AI assistant." }], dynamicVariables: {} },
    {
      status: "in-progress",
      transcript: [{ role: "agent", message: "Hi, this is an AI assistant." }, { role: "user", message: "I can do $395." }],
      dynamicVariables: {},
    },
    {
      status: "done",
      transcript: [{ role: "agent", message: "Hi, this is an AI assistant." }, { role: "user", message: "I can do $395." }],
      dynamicVariables: {},
    },
  ];
  const voice = new ScriptedVoice(frames);
  const { app, events } = setup(voice);
  const resp = await post(app, "/calls", {
    jobId: "j1", to: TEAM,
    openingContext: { bestTotal: 410, items: [{ name: "nitrile gloves", quantity: 2000, unit: "glove" }] },
  });
  assert.equal(resp.status, 200);
  assert.equal(voice.variables.best_total, "$410.00");
  assert.equal(voice.variables.items_list, "2000 glove nitrile gloves");
  for (let i = 0; i < 100 && !events.some(([, e]) => e.type === "call_ended"); i++) await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(events.map(([, e]) => `${e.type}:${e.eventId}`), [
    "call_started:conv_1:started", "transcript_line:conv_1:t0", "transcript_line:conv_1:t1", "call_ended:conv_1:ended",
  ]);
  const ended = events.at(-1)![1];
  assert.equal(ended.outcome, "completed");
  assert.equal((ended.transcript as unknown[]).length, 2);
  assert.equal(events[2]![1].speaker, "supplier");
});

test("calls to a non-team number are refused", async () => {
  const { app } = setup(new ScriptedVoice([]));
  const resp = await post(app, "/calls", { jobId: "j1", to: "+18005550100", openingContext: { bestTotal: 1, items: [] } });
  assert.equal(resp.status, 403);
});

test("ElevenLabs post-call webhook needs a valid signature", async () => {
  const { app, events } = setup(new ScriptedVoice([]));
  const raw = JSON.stringify({
    type: "post_call_transcription",
    data: {
      conversation_id: "conv_x", status: "done",
      transcript: [{ role: "user", message: "Total $395 for everything." }],
      conversation_initiation_client_data: { dynamic_variables: { job_id: "j7" } },
    },
  });
  const ts = Math.floor(Date.now() / 1000);
  const sig = createHmac("sha256", "el-secret").update(`${ts}.${raw}`).digest("hex");
  const bad = await app.request("/elevenlabs/post-call", { method: "POST", body: raw, headers: { "ElevenLabs-Signature": `t=${ts},v0=00` } });
  assert.equal(bad.status, 401);
  const good = await app.request("/elevenlabs/post-call", { method: "POST", body: raw, headers: { "ElevenLabs-Signature": `t=${ts},v0=${sig}` } });
  assert.equal(good.status, 200);
  assert.equal(events[0]![1].jobId, "j7");
  assert.equal(events[0]![1].eventId, "conv_x:ended");
  assert.equal(verifyElevenLabsSignature(raw, `t=${ts - 4000},v0=${sig}`, "el-secret"), false);
});

test("forwarder sends the webhook secret header and retries on 5xx", async () => {
  const calls: { url: string; secret: string | null }[] = [];
  let n = 0;
  const fakeFetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, secret: new Headers(init.headers).get("X-Webhook-Secret") });
    n += 1;
    return new Response("{}", { status: n === 1 ? 503 : 200 });
  }) as unknown as typeof fetch;
  const ok = await makeForwarder(config(), fakeFetch)("photon", { eventId: "e1", jobId: "j", type: "sms_in" });
  assert.equal(ok, true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0]!.url, "http://backend.test/webhooks/photon");
  assert.equal(calls[0]!.secret, "wh");
});
