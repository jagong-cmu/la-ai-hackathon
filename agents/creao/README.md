# CREAO agents

This folder tells you how to make the two CREAO agents on https://agent.creao.ai.

| Agent | Starts from | Job |
|---|---|---|
| **Supplier Discovery** | The backend, through the API Trigger | Finds 8 to 10 real local suppliers for one category and ZIP code. Returns a fixed JSON list. |
| **Daily Restock Check** | A CREAO scheduled run, once a day | Calls `POST {PUBLIC_BASE_URL}/planner/check?autostart=true` and writes a short summary. |

The backend client is `backend/app/discovery.py`. Its tests are in `backend/tests/test_discovery_creao.py`.

> **Warning:** Every run uses CREAO credits. Do not let one agent start another agent. Do not put an API key in the frontend, in this repo, or in a chat message.

## API facts (from docs.creao.ai, checked 2026-10-16)

The backend uses the **app-scoped API Trigger**:

| Item | Value |
|---|---|
| Key | `capi_...`. You make it on the agent detail page. It runs only that agent. |
| Auth header | `Authorization: Bearer capi_...` |
| Start a run | `POST https://agent.creao.ai/api/v1/apps/{appId}/runs`, body `{"inputs": {...}}` |
| Start reply | `202` with `{"id", "status": "pending", "appId", "appName"}` |
| Poll a run | `GET https://agent.creao.ai/api/v1/apps/{appId}/runs/{runId}` |
| Status values | `pending`, `running`, `completed`, `failed`, `cancelled` |
| Output | `result.text` (final text). `result.chatText` is present when the chat reply is different. Files are in `result.artifacts`. |
| Failure detail | `errorCode` on the polled run |
| Error body | `{"error": "ERROR_CODE"}`. `401` means a bad or revoked key. `429` means rate limited (obey `Retry-After`). |
| Webhooks | Optional `webhookUrl`. CREAO rejects localhost and private addresses. We poll instead. |

The other API path (CREAO CLI, `cr_sk_` keys, `https://developer.creao.ai/v1/runs` with `agent_id` and `input`) is different. Do not mix the two request shapes.

## Part A: Supplier Discovery agent

You make a CREAO agent from a good chat session. Then you fix its inputs and instructions.

### A1. Get a good chat session

1. Sign in at https://creao.ai/signup?hackathon=ucla-ai.
2. Open a new chat on https://agent.creao.ai.
3. Paste the full prompt from section A3. Replace the three input lines with these test values:
   - category: `packaging_disposables`
   - zipCode: `49007`
   - items: `9 in hinged to-go box, kraft paper bag, nitrile gloves`
4. Wait for the run to end. Runs can take some minutes.
5. Make sure that the last message has one JSON block that agrees with section A4.
6. If the result is not correct, send a follow-up message that tells the agent what to fix.

### A2. Save the session as an agent

1. Click **Create Agent** in the chat input bar.
2. Set **Output format** to **Markdown**. Do not use **Dashboard**. The backend reads text, not a dashboard.
3. In **Instructions**, paste: `The final message must be only the JSON object from the output contract. No other text.`
4. Click **Start building**.
5. Set the agent name to `Supplier Discovery`.
6. Open the **Config** tab. Make sure that `config.yaml` has exactly three text inputs with these keys: `category`, `zipCode`, `items`.
7. If the keys are different, send a follow-up message: `Rename the inputs to exactly category, zipCode and items. All three are text fields.` Each follow-up makes a new agent version.
8. Open `skill.md` on the **Config** tab. Make sure that it has the rules and the output contract from section A3. If it does not, send the section A3 text as a follow-up message.
9. Set the **Default model** in the agent sidebar. Use a model that does web search well. The API runs use this model.
10. Set the write autonomy to **Ask first**. API runs cannot pause for approval, so this blocks all write actions in connected services. This agent must not write anything.
11. Do not connect Gmail, Slack, or other connectors to this agent. It does not need them.
12. Install packages only through **Create Agent** or **Update Environment**. CREAO does not save packages that you install in chat.

### A3. Agent prompt (instructions)

Paste this text. The backend sends the three inputs.

```text
You are the Supplier Discovery agent for a small food business.
You find real businesses that sell one category of supplies near one US ZIP code.

INPUTS
- category: {{category}}   (one of: food_ingredient, packaging_disposables, cleaning_safety,
  smallwares, equipment, service, other)
- zipCode: {{zipCode}}     (5-digit US ZIP code of the delivery address)
- items: {{items}}         (comma-separated item names that the business buys; can be empty)

TASK
1. Search the web for businesses that sell the category, and the items if given, to
   restaurants and bakeries. Include local stores near the ZIP code, regional distributors
   that deliver to that ZIP code, and national online suppliers that ship there.
2. Open each candidate's own website. Confirm that the business is real and that it sells
   the category now. Drop a candidate if you cannot confirm both.
3. For each kept supplier, read the website for: the business name, the home page URL,
   the public sales phone number, the public sales email, and whether prices are visible
   without an account (pricesPublic).
4. Return 8 to 10 suppliers. Put the best matches first: the items are sold, prices are
   public, and the supplier delivers to the ZIP code.

RULES
- List only real businesses that you checked on their own website. Never invent a
  business, a URL, a phone number or an email address.
- Use only contact details that the business publishes. If a value is not published,
  use null.
- Do not contact any supplier. Do not send email, SMS, forms, chat messages or calls.
- Do not make an account, log in, add to cart, start checkout or buy anything.
- Do not use any connector that sends or posts data.
- Do not start other agents or other runs.
- "url" is the supplier's own home page, with https://. Do not use a marketplace,
  directory, review site, map link or search link.
- "notes" is one or two plain sentences: what they sell that matches, minimum order or
  delivery facts if shown, and why you kept them.

OUTPUT CONTRACT
Your final message is ONLY one JSON object in a ```json code block. No text before or
after it. Use exactly these keys:

{"suppliers": [
  {"name": "string",
   "url": "https://...",
   "phone": "string or null",
   "email": "string or null",
   "notes": "string",
   "pricesPublic": true}
]}

If you find no supplier that passes the rules, return {"suppliers": []}.
```

If the agent editor does not fill `{{...}}` placeholders, the inputs still reach the agent as named form fields. Keep the input names the same.

### A4. Output contract

The backend parses the final message (`result.text`) with `_parse_creao_output`:

```json
{"suppliers": [
  {"name": "Westside Restaurant Supply",
   "url": "https://westside.example",
   "phone": "+1 310 555 0100",
   "email": "sales@westside.example",
   "notes": "Sells hinged to-go boxes and kraft bags. Delivers to 49007.",
   "pricesPublic": true}
]}
```

Parser behavior:

- It reads the first ```json block. If there is no block, it reads the first JSON object that has a `suppliers` key, or a bare JSON list.
- It drops a row that has no `http://` or `https://` URL.
- It keeps `pricesPublic` and also stores it as `prices_public`.
- An empty list, no JSON, or a wrong shape is an error. Then the backend uses the cache, and after that the fixture list.

### A5. Connect the backend

1. Open the agent detail page.
2. Make an API key for this agent (the app-scoped `capi_` key). Copy it immediately.
3. Find the agent ID (`appId`). It is in the API Trigger example on the agent page. It is also in the agent page URL.
4. Put these values in the repo root `.env`. Do not commit `.env`.

   ```bash
   CREAO_API_KEY=capi_...      # server-side only
   CREAO_APP_ID=<agent appId>
   # Optional. These are the defaults:
   # CREAO_BASE_URL=https://agent.creao.ai
   # CREAO_POLL_S=10
   # CREAO_MAX_WAIT_S=300
   ```

5. Restart the backend. The `creao` mode changes to `live` when both values are set.
6. Test one run from a terminal. This uses credits.

   ```bash
   curl -sS -X POST "https://agent.creao.ai/api/v1/apps/$CREAO_APP_ID/runs" \
     -H "Authorization: Bearer $CREAO_API_KEY" -H "Content-Type: application/json" \
     -d '{"inputs":{"category":"packaging_disposables","zipCode":"49007","items":"to-go box"}}'
   # Copy "id" from the reply, then poll every 10 s:
   curl -sS "https://agent.creao.ai/api/v1/apps/$CREAO_APP_ID/runs/<id>" \
     -H "Authorization: Bearer $CREAO_API_KEY"
   ```

7. Make sure that `status` is `completed` and that `result.text` has the JSON block.

Backend behavior:

- The backend caches results by category and ZIP code. It runs CREAO only in `live` mode.
- It polls every `CREAO_POLL_S` seconds for at most `CREAO_MAX_WAIT_S` seconds.
- It sends `idempotencyKey = "disc-" + hash(category, zip, date)`. If CREAO rejects the key with `400` or `422`, the backend sends the request again without the key.
- On a failed run, a timeout, an auth error or bad output, it uses the cache. If there is no cache, it uses `backend/fixtures/discovery.json`.

## Part B: Daily Restock Check agent (scheduled)

This agent calls the backend once a day. The backend checks which supplies need an order. With `autostart=true`, it also starts a sourcing job.

> **Caution:** Scheduled runs need the **Pro Plus** plan or higher. On Free or Pro, the schedule is saved but does not run.

> **Caution:** The backend must be reachable on a public HTTPS URL (the tunnel). The CREAO sandbox cannot reach `localhost`.

### B1. Store the URL and token as CREAO secrets

1. Click **Secrets** in the CREAO sidebar.
2. Click **Add secret**. Name: `RESTOCK_BASE_URL`. Value: the public tunnel URL, for example `https://<tunnel-host>`. No slash at the end.
3. Click **Add secret** again. Name: `RESTOCK_TOKEN`. Value: a long random string. Today the backend does not check this token. Add the check before the demo.
4. Click **Save secret** each time.

Secret names use only uppercase letters, digits and underscores. CREAO injects each secret into the agent sandbox as an environment variable (`os.environ["RESTOCK_BASE_URL"]` in Python, `process.env.RESTOCK_BASE_URL` in Node.js). Secrets are per account, so every agent of the account can read them.

### B2. Make the agent

1. Open a new chat on https://agent.creao.ai.
2. Paste the prompt from section B4.
3. Make sure that the run calls the endpoint and writes the summary.
4. Click **Create Agent**. Set **Output format** to **Markdown**.
5. Set the agent name to `Daily Restock Check`. This agent has no inputs.
6. Open the **Config** tab. Make sure that `skill.md` has the B4 rules.
7. Set write autonomy to **Ask first**. The agent needs no connectors.

### B3. Make the schedule

1. Open the agent. Click the **Schedule** tab.
2. Click **+ Create Schedule**.
3. Set **Frequency** to **Daily**. Set **Time** to `06:00`. Set **Timezone** to `America/Los_Angeles`.
4. Optional: set **Delivery** to **Email** to get a summary after each run.
5. Click **Create Schedule**. The next run time shows on the agent page.
6. For the demo, click **Run** on the agent to start one run now.

Runs must be at least 5 minutes apart. Each scheduled run uses at least 0.1 credits. If the account has no credits, CREAO skips the run. After 2 skipped runs in a row, CREAO pauses the schedule. The **Sessions** tab shows every run.

### B4. Agent prompt (instructions)

```text
You are the Daily Restock Check agent for a small food business.
You do one HTTP call and write a short report. Nothing more.

STEPS
1. Read the environment variables RESTOCK_BASE_URL and RESTOCK_TOKEN.
   If RESTOCK_BASE_URL is missing, stop and report "RESTOCK_BASE_URL secret is not set".
2. Send exactly one request with Python:

   import os, requests
   r = requests.post(
       os.environ["RESTOCK_BASE_URL"].rstrip("/") + "/planner/check",
       params={"autostart": "true"},
       headers={"Authorization": "Bearer " + os.environ.get("RESTOCK_TOKEN", "")},
       timeout=60,
   )
   print(r.status_code)
   print(r.text[:4000])

3. If the request fails (connection error, timeout, or a status that is not 2xx), do not
   retry more than once. Report the status code and the first 300 characters of the body.
4. If the status is 200, the body is JSON:
   {"due": [{"name", "recommended_qty", "unit", "status", ...}], "job_id": string|null,
    "message": string}

REPORT (Markdown, at most 10 lines)
- Line 1: "Restock check: <message>"
- Then one bullet for each item in "due": name, recommended quantity, unit, status.
- Last line: "Sourcing job started: <job_id>" if job_id is not null,
  else "No sourcing job started."

RULES
- Never print or repeat the value of RESTOCK_TOKEN or any other secret.
- Call only RESTOCK_BASE_URL + "/planner/check". Do not call any other endpoint.
- Do not contact suppliers. Do not buy anything. Do not start other agents or runs.
```

## Part C: Devpost links (CREAO track requirement)

The Devpost README must have the **agent ID** and a **share link to one agent run**.

1. **Agent ID:** Use the `appId` from step A5.3. Use the same value as `CREAO_APP_ID`. Never put the API key in the README.
2. **Run share link:**
   1. Open the **Supplier Discovery** agent. Open the **Sessions** tab.
   2. Open a completed run with a good result. This opens the run's chat thread.
   3. Click the share icon in the thread header. CREAO makes a read-only public link.
   4. Copy the link into the Devpost README.
3. Also add a share link for one **Daily Restock Check** run. This shows the scheduled part.

> **Caution:** Anyone with a share link can see the full thread and its files. Read the thread first. Make sure that it shows no secret, no tunnel URL that you want to keep private, and no private data. You can revoke a share link later.

Devpost text example:

```markdown
**CREAO agent ID:** `<appId>`
**Example run:** <thread share link>
```

## Facts that the docs do not confirm

- `idempotencyKey`, `threadId` and `chatModelId` on the app-scoped start request. The current docs show only `inputs` and `webhookUrl`. The backend sends `idempotencyKey` and retries without it on `400` or `422`.
- The full JSON shape of the polled app-scoped run. The docs show `status`, `result.text`, `result.chatText`, `result.artifacts` and `errorCode`. The backend also reads older shapes (`output`, `result` as a string).
- A cancel endpoint for the app-scoped path. The backend does not cancel a run that times out.
- Rate limits for the app-scoped path. The docs give no numbers. The backend obeys `Retry-After` on `429` while it polls.
- The exact location of the agent ID in the UI. The API Trigger example on the agent page shows it.
