# Photon service (voice and messaging bridge)

A small Node + TypeScript service (Hono, `spectrum-ts`, `pnpm`). It owns the Photon line (texts) and starts the voice calls. A call goes from ElevenLabs Agents through the Photon SIP trunk. The backend's live distributor channel (`backend/app/distributor/live.py`) calls this service, and the service sends every inbound event back to the backend.

```
backend LiveDistributor ──POST /sms, /calls──▶ photon service ──spectrum-ts──▶ Photon line ──▶ team phone
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
- If a key is missing, that part runs in dry-run mode. Texts are logged with the last 4 digits only. Calls are reported as `no_answer`. With no keys, nothing leaves the machine.
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
| `PHOTON_PROJECT_ID` | service | texts (Spectrum project ID) |
| `PHOTON_PROJECT_SECRET` | service | texts (Spectrum project secret). `PHOTON_API_KEY` is accepted as an alias. |
| `PHOTON_WEBHOOK_SECRET` | service | only for `PHOTON_INBOUND_MODE=webhook` |
| `PHOTON_INBOUND_MODE` | service | `stream` (default: no public URL is necessary), `webhook`, or `off` |
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

### 1. Photon (https://photon.codes, Spectrum)
1. Create a project. Copy the **project ID** and the **project secret** into `PHOTON_PROJECT_ID` and `PHOTON_PROJECT_SECRET`.
2. Get a line. On the Free and Pro plans, the project uses a shared pool of numbers, and shared lines cannot take voice calls. For voice, you need a voice-capable number (a dedicated line, Business plan). A new line becomes active after the next token renewal, or when you restart the service.
3. Create a **Voice profile** on that number. Under **Outbound calls**, select SHA-256 digest, enable outbound, and save the password. Photon shows the password one time only.
4. Inbound destination (for calls that come in to the line, optional): `sips:sip.rtc.elevenlabs.io:5061`, TLS.
5. Inbound texts: the default `stream` mode needs no URL. For webhook mode, set the Spectrum webhook to `https://<photon-tunnel>/photon/webhook`, put the signing secret in `PHOTON_WEBHOOK_SECRET`, and set `PHOTON_INBOUND_MODE=webhook`.

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

The default setup (Spectrum stream, ElevenLabs polling) needs no public URL. For the optional webhooks, start a quick tunnel to this service:

```bash
cloudflared tunnel --url http://localhost:8787
# copy the printed https://<name>.trycloudflare.com URL into the Photon and ElevenLabs webhook settings
```

If an external provider must reach the backend directly, use `cloudflared tunnel --url http://localhost:8000` and put the URL in `PUBLIC_BASE_URL`.

## What the docs confirm and what they do not

| Topic | Status |
|---|---|
| `spectrum-ts` 12.11.0: `Spectrum({projectId, projectSecret, providers:[imessage.config()]})`, `imessage(app).user(phone)`, `im.space.create(user)`, `space.send(text)`, `app.messages` stream, `app.webhook()` with `X-Spectrum-Signature` | Confirmed (docs and the package types; `pnpm typecheck` passes) |
| The Spectrum provider is **iMessage**. A recipient can have the service `SMS`, but the docs do not describe an automatic SMS fallback. | Not confirmed for non-iMessage phones. Test it with the team phone. |
| The inbound `message.sender.id` is the sender's E.164 phone | Not confirmed (TODO in `src/sms.ts`) |
| Photon voice = SIP trunk only. The SDK has no call control ("coming soon"). ElevenLabs places the call. | Confirmed |
| ElevenLabs `POST /v1/convai/sip-trunk/outbound-call` with `dynamic_variables` and `ringing_timeout_secs` | Confirmed |
| ElevenLabs `GET /v1/convai/conversations/{id}`: the status and the transcript | Confirmed. It is **not** confirmed that the transcript fills in during the call. If it does not, the transcript arrives with `call_ended`, and the backend replays it. |
| Real-time monitor WebSocket | Confirmed, but **Enterprise only**. It is not used (TODO). |
| Post-call webhook `post_call_transcription`, `ElevenLabs-Signature: t=..,v0=..` | Confirmed. The `call_initiation_failure` data fields are not confirmed. |
| Custom LLM over OpenRouter (`stepfun/step-5-preview`) | Not confirmed. The docs confirm any OpenAI-compatible URL. |
