import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import type { BackendEvent } from "./backend.js";
import { makeForwarder } from "./backend.js";
import type { Config } from "./config.js";
import { createApp } from "./server.js";
import {
  createMessenger,
  DryRunMessenger,
  type EventSocket,
  type Messenger,
  PhotonMessenger,
  signPhotonWebhook,
  verifyPhotonWebhook,
} from "./sms.js";
import { type Conversation, fillVariables, verifyElevenLabsSignature, type VoiceApi } from "./voice.js";

const TEAM = "+15551234567";

function config(): Config {
  return {
    port: 0,
    backendUrl: "http://backend.test",
    webhookSecret: "wh",
    serviceToken: "",
    allowedPhones: new Set([TEAM]),
    photon: {
      projectId: "", apiKey: "", phoneNumber: "", baseUrl: "http://photon.test", webhookSecret: "",
      inbound: "off", pollMs: 5,
    },
    elevenlabs: {
      apiKey: "", agentId: "", agentPhoneNumberId: "", baseUrl: "http://el.test", webhookSecret: "el-secret",
      toolSecret: "tool-secret", ringingTimeoutS: 20, pollMs: 5, callMaxS: 2,
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

function setup(voice: VoiceApi, messenger: Messenger = new DryRunMessenger(), cfg: Config = config()) {
  const events: [string, BackendEvent][] = [];
  const forward = async (provider: "photon" | "elevenlabs", e: BackendEvent) => {
    events.push([provider, e]);
    return true;
  };
  const { app, onInboundText } = createApp({ config: cfg, messenger, voice, forward });
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

test("sms with fields that are not strings is a 400, not a crash", async () => {
  // Soak S-026: text: 5 passed the `!body.text` check and then body.text.slice() threw (500).
  const { app } = setup(new ScriptedVoice([]));
  for (const body of [{ jobId: "j1", to: TEAM, text: 5 }, { jobId: "j1", to: 5551234567, text: "hi" },
    { jobId: { a: 1 }, to: TEAM, text: "hi" }, [1, 2], null]) {
    assert.equal((await post(app, "/sms", body)).status, 400, JSON.stringify(body));
  }
  const ctx = { bestTotal: 100, items: [] };
  for (const body of [{ jobId: "j1", to: 5551234567, openingContext: ctx }, { jobId: 7, to: TEAM, openingContext: ctx }, null]) {
    assert.equal((await post(app, "/calls", body)).status, 400, JSON.stringify(body));
  }
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

class GreetingVoice extends ScriptedVoice {
  async firstMessage(variables: Record<string, string | number>) {
    return fillVariables("Hi, this is an AI assistant for {{business_name}}.", variables);
  }
}

test("the live-line tool forwards each turn at once and replaces the polled lines", async () => {
  // The call stays in progress until the end, so the poller would send t0/t1 if no live lines came.
  const talk = [{ role: "agent", message: "Hi." }, { role: "user", message: "Hello." }];
  const frames: Conversation[] = [
    { status: "initiated", transcript: [], dynamicVariables: {} },
    { status: "initiated", transcript: [], dynamicVariables: {} },
    { status: "in-progress", transcript: talk, dynamicVariables: {} },
  ];
  const voice = new GreetingVoice(frames);
  const { app, events } = setup(voice);
  await post(app, "/calls", { jobId: "j1", to: TEAM, openingContext: { bestTotal: 410, items: [] } });
  const live = (body: unknown, secret = "tool-secret") =>
    app.request("/elevenlabs/live-line", {
      method: "POST", headers: { "content-type": "application/json", "X-Tool-Secret": secret }, body: JSON.stringify(body),
    });

  assert.equal((await live({ conversation_id: "conv_1", agent_reply: "Hi." }, "wrong")).status, 401);
  const turn = { conversation_id: "conv_1", supplier_said: "Sure, what's up?", agent_reply: "Price for 500 boxes?" };
  // ElevenLabs sometimes calls the tool twice for one turn, at the same moment.
  const [first, again] = await Promise.all([live(turn), live(turn)]);
  assert.deepEqual([await first.json(), await again.json()], [{ ok: true }, { ok: true }]);
  await live({ conversation_id: "conv_1", supplier_said: " ", agent_reply: "Are you there?" });
  assert.equal((await live({ conversation_id: "conv_other", agent_reply: "x" })).status, 200);

  await new Promise((r) => setTimeout(r, 40)); // let the poller reach the in-progress frames
  assert.deepEqual(events.map(([, e]) => `${e.type}:${e.eventId}`), [
    "call_started:conv_1:started", "transcript_line:conv_1:live0", "transcript_line:conv_1:live1",
    "transcript_line:conv_1:live2", "transcript_line:conv_1:live3",
  ]);
  assert.deepEqual(
    events.slice(1).map(([, e]) => [e.speaker, e.text, e.live]),
    [
      ["agent", "Hi, this is an AI assistant for Hearthloaf Bakeries.", true],
      ["supplier", "Sure, what's up?", true],
      ["agent", "Price for 500 boxes?", true],
      ["agent", "Are you there?", true],
    ],
  );
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

// ---------- Photon 2.0 beta SMS (fake HTTP; nothing leaves the process) ----------

const PRJ = "pho_prj_01234567890abcdefghjkmnpqr";
const OURS = "+15557654321";
const AGENT = "pho_usr_0aaaaaaaaaaaaaaaaaaaaaaaaa";
const HUMAN = "pho_usr_0bbbbbbbbbbbbbbbbbbbbbbbbb";
const SPACE = "pho_spc_0ccccccccccccccccccccccccc";
const MSG_OUT = "pho_msg_0ddddddddddddddddddddddddd";
const MSG_IN = "pho_msg_0eeeeeeeeeeeeeeeeeeeeeeeee";
const NOW = "2026-10-17T00:00:00.000Z";
const SECRET = `whsec_${Buffer.from("test-signing-secret-32-bytes-long").toString("base64")}`;

function photonConfig(over: Partial<Config["photon"]> = {}): Config {
  const c = config();
  c.photon = { ...c.photon, projectId: PRJ, apiKey: "pho_sk_test", phoneNumber: OURS, webhookSecret: SECRET, ...over };
  return c;
}

const user = (id: string, handle: string, kind: string) => ({
  id, projectId: PRJ, platform: "sms", handle, kind, details: {}, metadata: {}, createdAt: NOW, updatedAt: NOW,
});
const snapshot = (id: string, handle: string, kind: string) => ({
  id, projectId: PRJ, platform: "sms", handle, kind, details: {}, metadata: {},
});
const spaceSnap = { id: SPACE, projectId: PRJ, platform: "sms", kind: "direct", participantIds: [AGENT, HUMAN], details: {}, metadata: {} };

function receivedMessage(id: string, from: string, text: string) {
  return {
    id, projectId: PRJ, platform: "sms", status: "received",
    space: spaceSnap, sentBy: snapshot(HUMAN, from, "human"), receivedBy: snapshot(AGENT, OURS, "agent"),
    content: { type: "text", format: "plain", text }, metadata: {}, createdAt: NOW, updatedAt: NOW,
    timestamps: { receivedAt: NOW },
  };
}

interface Seen { method: string; path: string; headers: Headers; body: any }

function fakePhoton() {
  const requests: Seen[] = [];
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const url = new URL(req.url);
    const raw = req.method === "GET" ? "" : await req.text();
    const body = raw ? JSON.parse(raw) : undefined;
    requests.push({ method: req.method, path: url.pathname + url.search, headers: req.headers, body });
    if (req.method === "POST" && url.pathname === `/v1/projects/${PRJ}/users`) {
      return body.handle === OURS ? json(200, user(AGENT, OURS, "agent")) : json(201, user(HUMAN, body.handle, "human"));
    }
    if (req.method === "POST" && url.pathname === `/v1/projects/${PRJ}/spaces`) {
      return json(201, { ...spaceSnap, createdAt: NOW, updatedAt: NOW });
    }
    if (req.method === "POST" && url.pathname === `/v1/projects/${PRJ}/messages`) {
      return json(202, {
        id: MSG_OUT, projectId: PRJ, platform: "sms", status: "dispatched", space: spaceSnap,
        sentBy: snapshot(AGENT, OURS, "agent"), content: body.content, metadata: {},
        createdAt: NOW, updatedAt: NOW, timestamps: { dispatchedAt: NOW },
      });
    }
    if (req.method === "GET" && url.pathname === `/v1/projects/${PRJ}/messages`) {
      return json(200, { items: [receivedMessage(MSG_IN, TEAM, "Gloves $250")] });
    }
    return json(404, { type: "about:blank", code: "NOT_FOUND", title: "Not Found", status: 404 });
  }) as typeof fetch;
  return { requests, fetchImpl };
}

test("dry-run when the Photon key, project or number is missing", async () => {
  assert.equal((await createMessenger(config())).mode, "dry-run");
  assert.equal((await createMessenger(photonConfig({ apiKey: "" }))).mode, "dry-run");
  assert.equal((await createMessenger(photonConfig({ phoneNumber: "" }))).mode, "dry-run");
  const { app } = setup(new ScriptedVoice([]));
  const resp = await post(app, "/sms", { jobId: "j1", to: TEAM, text: "hi" });
  assert.deepEqual(await resp.json(), { messageId: "dry_1", mode: "dry-run" });
});

test("sending maps to the beta users -> spaces -> messages requests", async () => {
  const { requests, fetchImpl } = fakePhoton();
  const messenger = await createMessenger(photonConfig(), { fetch: fetchImpl, retry: false });
  assert.equal(messenger.mode, "photon");
  const { app } = setup(new ScriptedVoice([]), messenger, photonConfig());
  const resp = await post(app, "/sms", { jobId: "j1", to: TEAM, text: "Quote for 2000 gloves?" });
  assert.equal(resp.status, 200);
  assert.deepEqual(await resp.json(), { messageId: MSG_OUT, mode: "photon" });

  const users = requests.filter((r) => r.path.endsWith("/users"));
  assert.deepEqual(users.map((r) => r.body).sort((a, b) => a.handle.localeCompare(b.handle)), [
    { platform: "sms", handle: TEAM }, { platform: "sms", handle: OURS },
  ].sort((a, b) => a.handle.localeCompare(b.handle)));
  const space = requests.find((r) => r.path.endsWith("/spaces"))!;
  assert.deepEqual(space.body, { platform: "sms", participantIds: [AGENT, HUMAN] });
  assert.ok(space.headers.get("idempotency-key"));
  const send = requests.find((r) => r.path === `/v1/projects/${PRJ}/messages`)!;
  assert.equal(send.method, "POST");
  assert.deepEqual(send.body, { spaceId: SPACE, senderId: AGENT, content: { type: "text", format: "plain", text: "Quote for 2000 gloves?" } });
  assert.match(send.headers.get("idempotency-key") ?? "", /^sms-out:/);
  assert.equal(send.headers.get("authorization"), "Bearer pho_sk_test");

  // A second text to the same phone reuses the cached user and space.
  await post(app, "/sms", { jobId: "j1", to: TEAM, text: "Any update?" });
  assert.equal(requests.filter((r) => r.path.endsWith("/spaces")).length, 1);
  assert.equal(requests.filter((r) => r.path === `/v1/projects/${PRJ}/messages`).length, 2);
});

test("a non-team phone is refused before any Photon request", async () => {
  const { requests, fetchImpl } = fakePhoton();
  const messenger = await createMessenger(photonConfig(), { fetch: fetchImpl, retry: false });
  const { app } = setup(new ScriptedVoice([]), messenger, photonConfig());
  assert.equal((await post(app, "/sms", { jobId: "j1", to: "+18005550100", text: "hi" })).status, 403);
  assert.equal(requests.length, 0);
});

function signedWebhook(body: string, secret = SECRET, ts = Math.floor(Date.now() / 1000)) {
  const id = "msg_test_1";
  return {
    method: "POST",
    body,
    headers: {
      "content-type": "application/json",
      "webhook-id": id,
      "webhook-timestamp": String(ts),
      "webhook-signature": signPhotonWebhook(secret, id, String(ts), body),
    },
  };
}

const inboundEvent = (id = MSG_IN) =>
  JSON.stringify({
    id: "pho_evt_0fffffffffffffffffffffffff", type: "message.received", apiVersion: "2026-07-01",
    source: "sms.photon", timestamp: NOW, data: { projectId: PRJ, object: receivedMessage(id, TEAM, "Gloves $250") },
  });

test("an inbound webhook with a valid signature produces one sms_in", async () => {
  const { fetchImpl } = fakePhoton();
  const messenger = await createMessenger(photonConfig(), { fetch: fetchImpl, retry: false });
  const { app, events } = setup(new ScriptedVoice([]), messenger, photonConfig());
  await post(app, "/sms", { jobId: "j42", to: TEAM, text: "hi" });
  const body = inboundEvent();
  assert.equal((await app.request("/photon/webhook", signedWebhook(body))).status, 200);
  // A retry of the same delivery is not forwarded again.
  assert.equal((await app.request("/photon/webhook", signedWebhook(body))).status, 200);
  assert.deepEqual(events, [
    ["photon", { eventId: `sms:${MSG_IN}`, jobId: "j42", type: "sms_in", text: "Gloves $250", messageId: MSG_IN }],
  ]);
});

test("an inbound webhook with a bad or stale signature is rejected", async () => {
  const messenger = await createMessenger(photonConfig(), { fetch: fakePhoton().fetchImpl, retry: false });
  const { app, events } = setup(new ScriptedVoice([]), messenger, photonConfig());
  const body = inboundEvent();
  const wrongKey = `whsec_${Buffer.from("another-secret").toString("base64")}`;
  assert.equal((await app.request("/photon/webhook", signedWebhook(body, wrongKey))).status, 401);
  const tampered = signedWebhook(body);
  tampered.body = body.replace("Gloves $250", "Gloves $1");
  assert.equal((await app.request("/photon/webhook", tampered)).status, 401);
  assert.equal((await app.request("/photon/webhook", { method: "POST", body })).status, 401);
  const stale = Math.floor(Date.now() / 1000) - 3600;
  assert.equal((await app.request("/photon/webhook", signedWebhook(body, SECRET, stale))).status, 401);
  assert.equal(verifyPhotonWebhook(body, { id: "x", timestamp: "1", signature: "v1,AAAA" }, ""), false);
  assert.equal(events.length, 0);
});

test("poll mode lists received SMS and forwards each message one time", async () => {
  const { requests, fetchImpl } = fakePhoton();
  const cfg = photonConfig({ inbound: "poll" });
  const messenger = new PhotonMessenger(cfg, { fetch: fetchImpl, retry: false });
  const { events, onInboundText, app } = setup(new ScriptedVoice([]), messenger, cfg);
  await post(app, "/sms", { jobId: "j5", to: TEAM, text: "hi" });
  const stop = await messenger.listen(onInboundText);
  for (let i = 0; i < 100 && requests.filter((r) => r.method === "GET").length < 3; i++) await new Promise((r) => setTimeout(r, 5));
  await stop();
  const list = requests.find((r) => r.method === "GET")!;
  assert.match(list.path, /direction=received/);
  assert.match(list.path, /platform=sms/);
  assert.deepEqual(events.map(([, e]) => e.eventId), [`sms:${MSG_IN}`]);
});

test("stream mode subscribes over Socket.IO and forwards message.received", async () => {
  const handlers = new Map<string, (...args: any[]) => void>();
  let options: Record<string, unknown> = {};
  let subscribed: unknown;
  const socket: EventSocket = {
    on: (event, listener) => handlers.set(event, listener),
    timeout: () => ({ emit: (_event, payload, ack) => { subscribed = payload; ack(null, { ok: true }); } }),
    connect: () => handlers.get("connect")!(),
    disconnect: () => undefined,
  };
  const cfg = photonConfig({ inbound: "stream" });
  const messenger = new PhotonMessenger(cfg, {
    fetch: fakePhoton().fetchImpl, retry: false,
    socketFactory: (_url, opts) => { options = opts; return socket; },
  });
  const { events, onInboundText, app } = setup(new ScriptedVoice([]), messenger, cfg);
  await post(app, "/sms", { jobId: "j6", to: TEAM, text: "hi" });
  const stop = await messenger.listen(onInboundText);
  assert.equal(options.path, `/v1/projects/${PRJ}/events/ws`);
  assert.deepEqual(options.extraHeaders, { Authorization: "Bearer pho_sk_test" });
  assert.deepEqual(subscribed, { startSeq: 0 });
  handlers.get("delivery")!(JSON.parse(inboundEvent()), { seq: 7, replyExpected: false });
  handlers.get("delivery")!(JSON.parse(inboundEvent()), { seq: 8, replyExpected: false }); // redelivery
  await stop();
  assert.deepEqual(events.map(([, e]) => `${e.type}:${e.eventId}:${e.jobId}`), [`sms_in:sms:${MSG_IN}:j6`]);
});
