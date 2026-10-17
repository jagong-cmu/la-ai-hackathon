# Photon service (voice and messaging bridge)

A small Node + TypeScript service (Hono, `@photon-ai/api` 0.5.0, `pnpm`, Node 24 or later). It uses the **Photon 2.0 beta** API. It owns the Photon SMS number (texts) and starts the voice calls. A call goes from ElevenLabs Agents through the Photon SIP trunk. The backend's live distributor channel (`backend/app/distributor/live.py`) calls this service, and the service sends every inbound event back to the backend.

```
backend LiveDistributor ──POST /sms, /calls──▶ photon service ──Photon beta API──▶ Photon SMS number ──▶ team phone
                                                        └──ElevenLabs outbound-call API──▶ ElevenLabs agent ──SIP (Photon trunk)──▶ team phone
backend /webhooks/{photon|elevenlabs} ◀── sms_in, call_started, transcript_line, call_ended ──┘
```

## Contract with the backend

**Backend → this service.** If `PHOTON_SERVICE_TOKEN` is set, each request must have the header `X-Service-Token`.

| Route | Body | Response |
|---|---|---|
| `POST /sms` | `{jobId, to, text}` | `{messageId, mode}`. 403 if `to` is not a team phone. 502 if the send fails. |
| `POST /calls` | `{jobId, to, openingContext: {bestTotal, items: [{id, name, quantity, unit}], businessName}}` | `{callId, status: "dialing", mode}`. `callId` is the ElevenLabs `conversation_id`. |
| `GET /health` | | `{ok, sms, voice, allowedPhones, activeCalls}` (no secrets) |

**This service → backend.** The service sends `POST {BACKEND_URL}/webhooks/photon` (texts) or `/webhooks/elevenlabs` (calls), with the header `X-Webhook-Secret: <WEBHOOK_SECRET>`. Each body is flat JSON:

| `type` | Fields | `eventId` |
|---|---|---|
| `sms_in` | `jobId, text, messageId` | `sms:<messageId>` |
| `call_started` | `jobId, callId` | `<callId>:started` |
| `transcript_line` | `jobId, callId, seq, speaker (agent or supplier), text` | `<callId>:t<seq>` |
| `call_ended` | `jobId, callId, outcome (completed, failed or no_answer), transcript: [{seq, speaker, text}], final: true` | `<callId>:ended` |

The event IDs are stable. The poller and the post-call webhook can both report the end of a call, and the backend keeps only one (it removes duplicates by `eventId`). Inbound texts have no job ID. The service sends a text to the job that last texted that phone.

## Guardrails (the code enforces them)

- `/sms` and `/calls` accept only `DEMO_DISTRIBUTOR_PHONE`, `OWNER_PHONE` and `PHOTON_ALLOWED_PHONES`. Any other number gets 403, and the service sends nothing. The backend also texts and calls only `DEMO_DISTRIBUTOR_PHONE`.
- If a key is missing, that part runs in dry-run mode. Texts need `PHOTON_API_KEY` (or `PHOTON_PROJECT_SECRET`), `PHOTON_PROJECT_ID` and `PHOTON_PHONE_NUMBER`. Texts are logged with the last 4 digits only. Calls are reported as `no_answer`. With no keys, nothing leaves the machine.
- The logs never print secrets or full phone numbers.
- In its first sentence, the voice agent says that it is an AI. It never agrees to buy. It never asks for payment details, and it does not record. See `elevenlabs/agent.md`.

## Run it

```bash
cd services/photon
pnpm install
pnpm dev          # watch mode, port 8787 (PHOTON_SERVICE_PORT)
pnpm typecheck    # tsc --noEmit
pnpm test         # node:test with fakes; places no calls and sends no texts
curl localhost:8787/health
```

The service reads the repo-root `.env`, then `services/photon/.env`. The backend uses the live channel only when `RUN_MODE=live` (dependency `phone` = `live`). In `cached` mode, the fixture channel runs.

## Environment variables

| Name | Used by | Required for |
|---|---|---|
| `PHOTON_API_KEY` | service | texts. The beta project API key (`pho_sk_...`). |
| `PHOTON_PROJECT_SECRET` | service | fallback alias for `PHOTON_API_KEY`. The service uses it only when `PHOTON_API_KEY` is empty. |
| `PHOTON_PROJECT_ID` | service | texts. The beta project ID (`pho_prj_...`). |
| `PHOTON_PHONE_NUMBER` | service | texts. The project's SMS number in E.164 (`+1...`). The service sends from this number. |
| `PHOTON_INBOUND_MODE` | service | `stream` (default), `poll`, `webhook`, or `off`. Only `webhook` needs a public URL. |
| `PHOTON_WEBHOOK_SECRET` | service | only for `webhook` mode. The destination signing secret (`whsec_...`). |
| `PHOTON_POLL_MS` | service | optional. The poll interval for `poll` mode (default 3000). |
| `PHOTON_API_BASE_URL` | service | optional. Default `https://api.photon.codes`. |
| `ELEVENLABS_API_KEY` | service | calls |
| `ELEVENLABS_AGENT_ID` | service | calls |
| `ELEVENLABS_AGENT_PHONE_NUMBER_ID` | service | calls (the SIP-trunk number imported in ElevenLabs) |
| `ELEVENLABS_WEBHOOK_SECRET` | service | only for the post-call webhook (optional) |
| `DEMO_DISTRIBUTOR_PHONE` | both | the only number that the distributor lane texts or calls |
| `OWNER_PHONE` | service | allowlist |
| `PHOTON_ALLOWED_PHONES` | service | optional extra team numbers, separated by commas |
| `BACKEND_URL` | service | default `http://localhost:8000` |
| `WEBHOOK_SECRET` | both | the shared secret for `/webhooks/*` |
| `PHOTON_SERVICE_URL` | backend | default `http://localhost:8787` |
| `PHOTON_SERVICE_TOKEN` | both | optional |
| `PHOTON_SERVICE_PORT`, `CALL_RINGING_TIMEOUT_S` (20), `CALL_MAX_S` (300), `ELEVENLABS_POLL_MS` (1500) | service | optional tuning |

## Setup (dashboard steps)

### 1. Photon 2.0 beta (https://app.beta.photon.codes)
1. Select the project. Open **Settings** and copy the **Project ID** (`pho_prj_...`) into `PHOTON_PROJECT_ID`.
2. Open **API keys** and create a key. Give it `project:read` and `project:write`. Copy the key (`pho_sk_...`) into `PHOTON_API_KEY`. The dashboard shows the key one time only.
3. Put the project's SMS number (E.164, for example `+15551234567`) in `PHOTON_PHONE_NUMBER`.
4. Make sure that the SMS number can send to the team phones. The API reference says that SMS needs "current Platforms authorization for the sender and every recipient". It also needs campaign (10DLC) eligibility, unless every recipient is in-network. Check the number's campaign status in the dashboard.
5. Voice: create a **Voice profile** on the same number. Under **Outbound calls**, select SHA-256 digest, enable outbound, and save the password. Photon shows the password one time only.
6. Inbound destination for calls (optional): `sips:sip.rtc.elevenlabs.io:5061`, TLS.
7. Inbound texts. Select one mode with `PHOTON_INBOUND_MODE`:
   - `stream` (default): the service opens a Socket.IO connection to `wss://api.photon.codes`, path `/v1/projects/{projectId}/events/ws`. You do not need a public URL.
   - `poll`: the service calls `GET /v1/projects/{projectId}/messages?direction=received&platform=sms` every `PHOTON_POLL_MS`. You do not need a public URL.
   - `webhook`: create a webhook destination with the URL `https://<photon-tunnel>/photon/webhook` and the event `message.received`. Put the signing secret (`whsec_...`) in `PHOTON_WEBHOOK_SECRET`. You need the tunnel (step 3).

The `/photon/webhook` route always checks the signature. In `stream` and `poll` mode, the route is not necessary. The service forwards each inbound message one time only, also when two paths deliver it.

### 2. ElevenLabs (https://elevenlabs.io/app/agents)
1. Create the agent from `elevenlabs/agent.md` (first message, system prompt, placeholder values for the dynamic variables). Copy the agent ID into `ELEVENLABS_AGENT_ID`.
2. Go to **Phone Numbers**, then **Import**, then **From SIP Trunk**:
   - Phone number: the Photon voice number (+E.164)
   - Outbound address: `sip.photon.codes:5061`, without a `sip:` prefix
   - Transport: TLS. Media encryption: Allowed.
   - Codecs: G.722, PCMU, PCMA
   - Username and password: from the Photon voice profile
   - Inbound allowed addresses: `0.0.0.0/0`
3. Assign the agent to the number. Copy the phone number ID into `ELEVENLABS_AGENT_PHONE_NUMBER_ID`.
4. Optional: set the post-call webhook to `https://<photon-tunnel>/elevenlabs/post-call`. Put its secret in `ELEVENLABS_WEBHOOK_SECRET`. The service also polls `GET /v1/convai/conversations/{id}`, so the webhook is a backup.
5. Make one test call from the dashboard to the team phone.

### 3. Tunnel (only for webhooks)

The default setup (Photon `stream` mode, ElevenLabs polling) needs no public URL. For the optional webhooks, start a quick tunnel to this service:

```bash
cloudflared tunnel --url http://localhost:8787
# copy the printed https://<name>.trycloudflare.com URL into the Photon and ElevenLabs webhook settings
```

If an external provider must reach the backend directly, use `cloudflared tunnel --url http://localhost:8000` and put the URL in `PUBLIC_BASE_URL`.

## What the docs confirm and what they do not

The Photon rows refer to the beta docs (`photon.codes/docs/beta/...`), the OpenAPI file `https://api.photon.codes/openapi.json`, the AsyncAPI file `https://api.photon.codes/asyncapi.yaml`, and the `@photon-ai/api` 0.5.0 types.

| Topic | Status |
|---|---|
| Auth: `Authorization: Bearer <pho_sk_ key>`, base URL `https://api.photon.codes`, project paths `/v1/projects/{pho_prj_ id}` | Confirmed |
| `@photon-ai/api` 0.5.0: `new Photon({headers, baseUrl, fetch, timeoutMs, retry})`, `photon.projects.createOrGetUser`, `createOrGetSpace`, `sendMessage`, `listMessages` | Confirmed (package types; `pnpm typecheck` passes) |
| Send flow: create or get the two SMS Users, create or get the SMS Space (`Idempotency-Key` required), then `POST /messages` with `spaceId`, `senderId` and `content` (`Idempotency-Key` required). The result is `202`: Photon accepted the send, but delivery is not confirmed. | Confirmed (OpenAPI) |
| Text content `{type: "text", format: "plain", text}` for SMS | Partly confirmed. The docs show `format: "plain"` for iMessage and email. The OpenAPI only requires a non-empty `format`. |
| SMS needs Platforms authorization for the sender and each recipient, and campaign (10DLC) eligibility unless every recipient is in-network | Confirmed (OpenAPI text). The status of our number is not known. |
| Inbound by Socket.IO stream (`delivery` events, `subscribe {startSeq}`) | Confirmed (beta WebSocket docs and AsyncAPI). Not tested against the live service. |
| Inbound by polling `listMessages` | Confirmed (beta iMessage walkthrough and OpenAPI) |
| Inbound event `message.received` with `data.object` = the Message (`platform: "sms"`, `status: "received"`, `sentBy.handle`, `receivedBy.handle`, `content`) | Confirmed (OpenAPI `SmsMessageReceivedEvent`) |
| `sentBy.handle` of an inbound SMS is the sender's E.164 phone | Not confirmed. The docs show E.164 handles for iMessage. |
| Webhook destinations: create with URL, `apiVersion` and events; the signing secret starts with `whsec_` | Confirmed (OpenAPI) |
| Webhook signature headers and algorithm | **Not confirmed.** The beta docs do not describe them. The service uses the Standard Webhooks scheme: `webhook-id`, `webhook-timestamp`, `webhook-signature: v1,<base64 HMAC-SHA256 of "id.timestamp.body">`, with the key from the base64 part of `whsec_...`, and a 5-minute limit. If real deliveries get 401, use `stream` or `poll` mode. |
| Photon voice = SIP trunk only. ElevenLabs places the call. | Confirmed (beta voice cookbook) |
| ElevenLabs `POST /v1/convai/sip-trunk/outbound-call` with `dynamic_variables` and `ringing_timeout_secs` | Confirmed |
| ElevenLabs `GET /v1/convai/conversations/{id}`: the status and the transcript | Confirmed. It is **not** confirmed that the transcript fills in during the call. If it does not, the transcript arrives with `call_ended`, and the backend replays it. |
| Real-time monitor WebSocket | Confirmed, but **Enterprise only**. It is not used (TODO). |
| Post-call webhook `post_call_transcription`, `ElevenLabs-Signature: t=..,v0=..` | Confirmed. The `call_initiation_failure` data fields are not confirmed. |
| Custom LLM over OpenRouter (`stepfun/step-5-preview`) | Not confirmed. The docs confirm any OpenAI-compatible URL. |
