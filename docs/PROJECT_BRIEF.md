# Supplier Quote Agent: Project Context & Implementation Brief

> **For the coding agent reading this:** This file holds all the context from the planning conversation for our LA Hacks project. Read the whole file before planning anything. It covers the product, every decision made so far (and why), the sponsor tracks we're targeting and their exact requirements, the architecture, the data, the team split, the timeline and the open questions. Where something is unconfirmed, it is marked **UNCONFIRMED**. Don't treat those items as facts. Check them or ask the team.
>
> Working name: **Supplier Quote Agent** (placeholder; the team hasn't chosen a final name).
> Prepared: October 16, 2026 (the night before the event).

---

## 0. TL;DR

- **What:** a team of AI agents that finds the cheapest *equivalent* supplier quote for a small business. It learns what the business buys from its accounting history. It gathers quotes from supplier **websites** (Browser Use), **phone calls** (Photon + ElevenLabs) and **texts/emails** (Photon), with supplier discovery and follow-ups done by **CREAO**. It ranks quotes by **landed cost per unit**, negotiates once using the best quote, and **buys only after the owner approves**. A live **mission-control dashboard designed in Paper** shows everything as it happens.
- **Demo business:** Hearthloaf Bakeries (company-001 in the Intuit QuickBooks hackathon dataset). It spends **$181,192/year on hamburger bun flats** (27,534 flats, 41 orders, average **$6.58/flat**).
- **Main LLM:** StepFun **Step 5 Preview** (`step-5-preview`), called on every run.
- **Core tracks:** CREAO, Browser Use, Photon (Best Voice Agent), Paper (Best Designed Hack), StepFun. **Proposed:** Visa. **Add-ons:** MLH ElevenLabs, Sentry. **Deferred:** Fetch.ai (add at hour 18 if time). **Optional:** Intuit/QuickBooks (only with a Jev key and spare time).
- **Team:** four people (P1–P4), about 24 hours of hacking. **No cap** on how many sponsor tracks one project can enter (confirmed by the team).

---

## 1. Event context

- **Event:** LA Hacks AI Hackathon 2026, UCLA. Fetch.ai's event page lists **Oct 17, 2026** at the **James West Alumni Center**, Los Angeles. Paper's page says **Oct 17–18, 2026**. Registration: https://ai.lahacks.com/ · Live site (tracks, schedule, FAQ): https://live.lahacks.com/
- **Build window:** about 24 hours. All times in this doc are relative hours from the start of hacking (H0–H24). **UNCONFIRMED:** the exact start and end times; map H-numbers to the real schedule.
- **Submission:** Devpost for all tracks, plus track-specific extras (see §6).
- **Confirmed by the team:** there is **no cap** on the number of company/sponsor challenges a project can enter.

---

## 2. How we got here (decision history)

Read this so you understand why the plan looks the way it does.

1. We first considered the **Workday** track (help new hires and interns ramp up). The team interviewed two former interns. The strongest finding was **multi-week delays getting system access** (one waited about 5 weeks; a ticket "resolved" with the wrong permission level). We explored an "access agent" idea. **Dropped** in favour of the current idea.
2. We considered the **Intuit/QuickBooks** track: "System One" models like **Jev** (typed, calibrated, fast decisions) and a synthetic accounting dataset. We explored collections/late-payment ideas. **Kept only as optional.** The dataset is still central as our source of purchase history.
3. **Current idea: supplier quote agent.** The team proposed it; it was refined in conversation.
4. **Fetch.ai:** was planned as the owner's interface (ASI:One chat) and coordination layer, plus an agent-to-agent "demo supplier agent." **Now deferred.** The team decided Fetch is not key to the product and would mostly be another frontend. **Our own Paper-designed frontend is the main interface.** At the hour-18 freeze, if the core run works, one person wraps the orchestrator for ASI:One (see §6.9 for exactly what that needs).
5. **CREAO:** chosen as core with **"Option A": CREAO does supplier discovery and scheduled follow-ups.** The Browser Use library (not CREAO's built-in browser) does the cart-total work, so both the CREAO and Browser Use tracks keep a distinct, real job.
6. **StepFun:** added. Step 5 Preview is the system's main LLM.
7. **Visa:** proposed (not yet confirmed by the team). Adds spending rules and a sandbox payment after approval.
8. **Photon vs ElevenLabs:** no conflict. Photon provides the phone line (SMS number + Voice profile); ElevenLabs is the voice agent on the call, connected over SIP. Both tracks apply.

---

## 3. The product

### 3.1 Problem and user
- **User:** owner or operator of a small or medium business that regularly buys supplies (demo: a bakery buying bun flats).
- **Problem:** finding a better supplier price takes hours: searching sites, requesting quotes, calling, comparing apples to oranges. Price comparison is also misleading:
  - Different **pack sizes**: $42 for 1,000 cups vs $38 for 800. The second is more expensive per cup.
  - **Hidden costs**: shipping, minimum order amounts, fees that only appear in the cart.
  - **Not-quite-equivalent products**: seeded vs plain buns, a different grade or size.
  - **Timing and reliability**: cheap doesn't help if it arrives late or is backordered.
- **Core insight:** compare **true landed cost per unit for an equivalent product, delivered on time**. Browser and voice are just the ways we gather information; the comparison is where the intelligence lives.

### 3.2 End-to-end user experience (8 steps)

| # | Step | What happens | Where | Tracks |
|---|---|---|---|---|
| 1 | Ask | Owner: "find me a cheaper supplier for hamburger bun flats." | Dashboard (ASI:One later, if Fetch is added) | Paper |
| 2 | Baseline | Orchestrator loads purchase history: $6.58/flat average, about 670 per order, about weekly orders. | Purchase history (QuickBooks data) | StepFun, Intuit data |
| 3 | Discover | CREAO finds suppliers, their site, phone, email, and whether prices are public. | Discovery agent | CREAO |
| 4 | Gather | **In parallel:** real cart totals on websites; quote requests by text and email. | Web + message agents | Browser Use, Photon |
| 5 | Negotiate | Voice agent calls a supplier, says it's an AI, asks them to beat the best quote so far. | Voice agent | Photon, ElevenLabs |
| 6 | Compare | Landed cost per unit, pack sizes normalized, look-alike products flagged, savings vs baseline. | Ranking engine | StepFun (Jev optional) |
| 7 | Approve | One card: ranked options, reasons, evidence, yearly savings. **Approve / Edit / Reject.** Nothing is bought without a yes. | Dashboard | Paper (Visa trust) |
| 8 | Order | Pay **only within the owner's spending rules** (sandbox), or hand over a ready cart. Schedule follow-ups and reorder reminders. | Checkout + discovery agents | Visa (proposed), CREAO |

**Throughout:** the mission-control dashboard shows every step live (suppliers found, browser screenshots, call transcript, quotes arriving, ranking updating).

**Edge behaviour:** if nothing beats the current price, say so: "keep your current supplier." Anything the agents were unsure about is clearly marked for the owner.

### 3.3 Demo script (about 3 minutes)
1. Open on the bakery: "$181K a year on bun flats, 41 orders, paying $6.58 on average." (Real numbers from the dataset.)
2. Owner makes the request on the dashboard. Discovery returns suppliers; cards appear.
3. Web agent checks supplier sites and shows **real cart totals** with screenshots (live, or a cached run labelled as such).
4. **Live call on speaker:** the voice agent calls a "supplier" (a teammate), says it's an AI, and asks them to beat the best quote. The ranking updates live when they do. *This is the wow moment.*
5. Comparison shows pack sizes normalized and a look-alike flagged (e.g., plain instead of seeded buns, or a 1,000-flat minimum).
6. Owner approves; (Visa) sandbox payment within spending rules; yearly savings shown.
7. *(Feedback-loop moment, simulated and labelled.)* "One week later": a synthetic bill shows the new supplier charged $6.45 instead of $6.29 and delivered a day late. The agent flags price creep, lowers the supplier's reliability score and starts a re-quote. "It doesn't just find a deal once; it holds suppliers to their quotes."

> The supplier names and quotes used in mockups so far (Ridgeview Foods $6.29, Cedar Row Wholesale $6.30, Harbor Supply Co, Bluepoint Distributors) are **fictional placeholders**, not real data.

---

## 4. Architecture

### 4.1 Components at a glance

```
Owner ──► Mission-control dashboard (Next.js, designed in Paper)
              │  ▲ live events (SSE)
              ▼  │
         Backend API + Event server (FastAPI) ◄── every agent posts events
              │
              ▼
         Orchestrator (Python service; brain = Step 5 Preview)
              │ 1) load baseline from purchase history
              │ 2) discovery
              ▼
         Discovery agent (CREAO, via API Trigger, polled)
              │ supplier list
              ▼ 3) fan out in parallel
   ┌──────────────┬──────────────────┬───────────────────┐
   Web agent       Message agent      Voice agent
   (Browser Use +  (Photon SMS +      (Photon SMS number →
    Playwright)     email)             SIP → ElevenLabs)
   └──────────────┴──────────────────┴───────────────────┘
              │ quotes (one shared format)
              ▼
         Quote store (SQLite) ──► Ranking engine (Python + decide())
              │  best quote ──► voice agent negotiation round ──► re-rank
              ▼
         Recommendation ──► Owner approval (Approve / Edit / Reject)
              ▼
         Checkout agent (proposed: Visa sandbox or Stripe test mode, within spending rules)
              ▼
         Feedback loop: new bill → price history, supplier scorecard, owner prefs, savings
```

**Fetch-ready seam:** the orchestrator sits behind three functions (`start_job`, `get_status`, `approve`) so a Fetch uAgent can call the same functions later (see §6.9).

### 4.2 Agents and services (detailed)

| Component | Role | Inputs → Outputs | Tech | Owner | Tracks |
|---|---|---|---|---|---|
| **Orchestrator** | The hub: plans the job, loads the baseline, calls discovery, fans out to the channels, triggers negotiation, assembles the recommendation, posts events | owner request → job, events, Ranking, recommendation | Python service (async), Step 5 Preview for planning and explanation | P1 | StepFun |
| **Discovery agent** | Finds suppliers for the item and location: site, phone, email, prices public or not. **Scheduled runs** chase non-replies and re-quote before the next expected order | SourcingJob → SupplierList | CREAO agent built at agent.creao.ai; called via API Trigger; results **polled** | P2 | CREAO |
| **Web agent** | For suppliers with public prices: opens search pages from URL templates, adds the item to the cart, reads subtotal, shipping and minimum, saves a screenshot as evidence, **always stops before checkout**. Runs sites in parallel; scripted Playwright paths for the 3–4 demo sites, AI agent for unfamiliar sites | SupplierList (web-capable) → Quotes | Browser Use + Playwright/Chromium; Step 5 Preview reads screenshots **if image input works (UNCONFIRMED)** | P2 | Browser Use, StepFun |
| **Message agent** | Sends quote requests by SMS and email, follows up, parses replies into quotes. Responds to CREAO's follow-up triggers | SupplierList → Quotes | Photon 2.0 (SMS + email); Step 5 Preview drafts and reads | P3 | Photon, StepFun |
| **Voice agent** | Calls suppliers; **opens by saying it's an AI** calling for the business; asks for a price on the exact item, pack size, quantity and delivery; **negotiates once** using the best quote so far; never agrees to buy; **no recording without consent**. Transcript → quote | Supplier + best quote → Quote + transcript | Photon SMS number with a Voice profile → **SIP** → ElevenLabs voice agent. Optionally point ElevenLabs at Step 5 Preview as its LLM if ElevenLabs allows a custom LLM (**UNCONFIRMED**) | P3 | Photon (Best Voice Agent), MLH ElevenLabs |
| **Ranking engine** | Normalizes pack sizes, computes landed cost per unit, same-product check with confidence, flags (minimum order, slow delivery, missing info), savings vs baseline, applies supplier scores and owner preferences | Quotes + baseline + scorecard + prefs → Ranking | Python; `decide()` on Step 5 Preview; Jev optional | P1 | StepFun (Intuit optional) |
| **Owner approval** | Ranked options, reasons (Step 5 Preview), evidence, savings; Approve / Edit / Reject | Ranking → ApprovalDecision | Dashboard (later also an ASI:One interactive card) | P1 + P4 | Paper, Visa (trust) |
| **Checkout agent** *(proposed)* | Pays only within owner rules: per-order limit, approved suppliers, monthly budget (defaults derived from the books). Sandbox only | ApprovalDecision → order record | Visa sandbox **(ask Visa what they offer)** or Stripe test mode | P1 | Visa |
| **Event server** | Agents POST events; the dashboard subscribes; approval actions go back to the orchestrator | events in → SSE out | FastAPI + server-sent events | P4 | Sentry (tracing) |
| **Mission-control dashboard** | Live view of every supplier, cart, call and quote; supplier detail; savings and approval; savings-to-date and supplier scorecard | SSE → UI | Next.js/React, designed in Paper; Sentry Session Replay | P4 | Paper, Sentry |

### 4.3 Orchestrator API (the seam everything plugs into)

```python
# shared/orchestrator_api.py (shape only; implement in agents/orchestrator/)
def start_job(item_id: str, quantity: int, business_id: str = "company-001") -> str: ...   # returns job_id
def get_status(job_id: str) -> "JobStatus": ...        # stage, suppliers, quotes, ranking, recommendation
def approve(job_id: str, choice: "ApprovalDecision") -> "OrderResult": ...
```
- The dashboard calls these via FastAPI routes now. A Fetch uAgent can call the same functions later.
- **Approval logic lives in the backend, not the frontend.**

### 4.4 Shared quote format (every channel produces exactly this)

| Field | Type | Example | Filled by |
|---|---|---|---|
| quote_id | str | q_0142 | channel |
| job_id | str | job_001 | channel |
| supplier_name | str | Ridgeview Foods *(fictional)* | channel |
| source | enum: web, sms, email, call | call | channel |
| item_id | str | item-00001 (bun flat, 8 ct) | from job |
| product_title | str | Seeded hamburger bun, 8 ct, flat of 12 | channel |
| pack_desc | str | 12 packs × 8 buns | channel |
| listed_price_usd | float | 6.49 | channel |
| shipping_usd | float (per order) | 0.0 | channel |
| min_order_qty | int | 200 | channel |
| delivery_days | int | 1 | channel |
| evidence | str (URL, screenshot path, transcript snippet) | calls/ridgeview.txt | channel |
| landed_unit_price_usd | float | 6.29 | ranking (P1) |
| match_confidence | float 0–1 | 0.97 | ranking (P1) |
| flags | list[str] | ["min_order_exceeds_need"] | ranking (P1) |
| captured_at | ISO timestamp | 2026-10-17T15:02:00Z | channel |

Use **Pydantic models** for all of these. uAgents uses Pydantic models too, so they carry over directly if Fetch is added later.

### 4.5 Message models
- **SourcingJob:** job_id, business_id, item_id, product description, quantity per order, baseline price, deadline
- **SupplierList:** job_id, suppliers[] (name, site, phone, email, prices_public: bool)
- **QuoteRequest / QuoteResult:** job_id, supplier, quotes[] (format above)
- **Ranking:** job_id, ranked quotes, recommended quote_id, reasons, savings per order, savings per year
- **ApprovalDecision:** job_id, action (approve | edit | reject), chosen quote_id, edits, decided_by, reason

### 4.6 Event stream (agents → event server → dashboard)

| Event | Posted by | Payload |
|---|---|---|
| job_started | orchestrator | job_id, item, baseline |
| supplier_found | discovery | supplier, contact, prices_public |
| browsing_started / cart_total | web | supplier, screenshot path, totals |
| message_sent / reply_received | message | supplier, channel, text |
| call_started / transcript_line / call_ended | voice | supplier, speaker, text, outcome |
| quote_saved | any channel | quote (shared format) |
| ranking_ready | ranking | Ranking |
| negotiation_started / negotiation_result | orchestrator / voice | supplier, before/after price |
| approved / rejected / edited | approval | ApprovalDecision |
| order_placed | checkout | order record |
| price_creep_detected / supplier_score_changed | feedback loop | supplier, old/new values |

Build a **fake event generator** in `fixtures/` so the dashboard can be built before the real agents work.

### 4.7 The one decision function

```python
def decide(question: str, options: list[str], context: str) -> tuple[str, float, bool]:
    """Returns (answer, confidence 0-1, used_llm). Backed by Step 5 Preview.
    Jev can take over routine decisions later without callers changing."""
```
Every yes/no or pick-one decision goes through this: page type, which link to click, same product?, reply intent (quote / question / decline / no reply), call outcome (quote / counter / refusal / hand off).

### 4.8 Data stores (SQLite)
- `quotes`: the shared quote format
- `price_history`: supplier, item_id, date, price, source
- `supplier_scorecard`: supplier, price_accuracy (quoted vs invoiced), on_time_rate, backorders
- `owner_prefs`: rule (e.g., "seeded only"), source decision, created_at
- `order_outcomes`: order_id, quoted price, invoiced price, promised date, delivered date
- `jobs`, `events`: for status and replay

### 4.9 Suggested repo layout

```
agents/orchestrator/    P1  (orchestrator service, start_job/get_status/approve)
agents/discovery/       P2  (CREAO API Trigger client, polling, prompts, agent ID, run links)
agents/web/             P2  (Browser Use + Playwright scripts, cart extraction)
agents/messaging/       P3  (Photon SMS/email; may be a TypeScript service, see below)
agents/voice/           P3  (Photon voice profile, ElevenLabs config, call script)
agents/checkout/        P1  (proposed Visa/Stripe sandbox)
ranking/                P1  (normalization, landed cost, scoring, feedback loop)
data/                   P1  (QuickBooks dataset loader, baseline computation)
events/                 P4  (FastAPI event server, SSE)
web/                    P4  (Next.js dashboard)
shared/                 quote format, message models, decide(), orchestrator API types
fixtures/               fake quotes, fake events, cached web runs, demo call scripts
fetch_adapter/          LATER (uAgent wrapper for ASI:One, if time at H18)
```

> **Language note:** Photon's SDK is **TypeScript** ("spectrum-ts", https://photon.codes/docs/spectrum-ts/introduction). Expect the messaging/voice side to be a small Node/TS service that talks to the Python backend over HTTP. Everything else is Python.

### 4.10 Environment / keys (`.env`, never committed)
`STEPFUN` / OpenRouter key for step-5-preview · `CREAO_API_KEY` (`capi_…`, **server-side only**) · Browser Use key (if using their cloud) · Photon project credentials · ElevenLabs API key · Sentry DSN · Stripe test key or Visa sandbox key (if Visa) · Jev/TypeSafe key (if granted) · later: ASI:One / Agentverse keys.

---

## 5. Data: the Intuit QuickBooks hackathon dataset

### 5.1 What it is
- Hugging Face: https://huggingface.co/datasets/CL251215/QuickBooks-Hackathon-2026/tree/main
- One file: `hackathon-100-participant.tar.gz` (26.7 MB → about 403 MB extracted, 2,302 files, mostly YAML).
- **100 synthetic small businesses**, one year of internally consistent books each (365 days ending early October 2026), generated by `qb-world-sim`. **No real data.**
- Layout: `hackathon-100/index.json`, `hackathon-100/DATA_README.md`, `hackathon-100/company-XXX/rwm-v4/world-<id>/`, containing:
  - `company.yaml`, `accounts.yaml`, `customers.yaml`, `vendors.yaml`, `employees.yaml`, `items.yaml`
  - `invoices.yaml`, `sales_receipts.yaml`, `customer_payments.yaml`, `bills.yaml`, `bill_payments.yaml`
  - `journal_entries.yaml`, `projects.yaml`, `bank_transactions.yaml`, `estimates.yaml`, `terms.yaml`, `payroll_runs.yaml`
  - `reports/` (`profit_and_loss.yaml`, `balance_sheet.yaml`, `ar_aging.yaml`, `ap_aging.yaml`), `manifest.yaml`, `SCHEMA.md` (field-level schema)
  - `hidden/` (simulation ground truth)
- Company mix: 22 wholesale, 22 project contractors, 12 supply-and-install, 16 recurring services, 14 point-of-sale, 10 appointment practices, 4 nonprofits. 71 industries, 3 size tiers. **7 companies are distressed.**
- Across all 100 companies: **2,089 purchase (bill) line items, 99 distinct purchased items.** Many service businesses buy no itemized goods.

### 5.2 Conventions that bite
- **IDs are company-scoped** (`cust-customer-3` exists in many companies). Always scope lookups by company.
- **Money is decimal strings** (`"1234.56"`) in `*_amount` fields.
- Bank feed `amount` already carries its sign; running balance is cumulative.
- **The bank feed matches the books exactly.** No messy data unless you inject it.
- `hidden/` is simulation context (personas, market context, management decisions). Don't score against its prose.
- Single-line invoices; no sales tax, credit memos or refunds; payroll is gross only; one year of history only.
- **Rules:** you may modify or extend the data, but **disclose what you changed and why** in the submission.

### 5.3 Demo company: company-001, Hearthloaf Bakeries
(Commercial Bakeries, wholesale profile, small tier.) Purchases computed from `bills.yaml` item lines:

| Item | Qty / year | Spend / year | Bills | Avg unit price |
|---|---|---|---|---|
| Hamburger Bun Flat, 8 ct | 27,534 | $181,192 | 41 | $6.58 (range $6.03–$7.11) |
| Croissant Tray, 24 ct | 10,242 | $94,851 | 29 | $9.26 |
| Pan Loaf Bread Case, White | 6,390 | $69,495 | 25 | $10.88 |
| Hot Dog Bun Flat, 8 ct | 4,048 | $45,568 | 19 | $11.26 |
| Cake Round, 10 in Vanilla | 1,909 | $26,178 | 13 | $13.71 |
| Artisan Boule Case | 1,274 | $19,764 | 6 | $15.51 |

- Current bun supplier: **Ironclad Larkspur Outfitters** (`vend-supplier`), terms **Net 7**. One bill memo reads "Partial backorder noted; balance to follow separately," a usable reliability signal.
- Average bun order is about 670 flats (27,534 / 41), roughly weekly.
- Example bill line: `item-00001 "Hamburger Bun Flat, 8 ct"`, quantity 600, unit_price "6.83", amount "4098.00", 2025-10-10.

### 5.4 How the data is used (inputs)

| Agent needs | Source | Example |
|---|---|---|
| What to shop for | `bills.yaml` item lines | 6 recurring items |
| Which item first | spend per item | Bun flats, $181K |
| Price to beat | avg + range of unit prices | $6.58 (range $6.03–$7.11) |
| Order quantity | qty per bill | about 670 |
| When to re-quote | dates between bills | about weekly |
| Current supplier terms and reliability | `vendors.yaml`, bill memos | Net 7; backorder memo |
| Default spending rules | monthly category spend, cash (`balance_sheet`, `bank_transactions`) | monthly budget for checkout |

### 5.5 Feedback loop (outputs → inputs)

```
Books (past bills) → baseline + shopping list → agents → ranking → approval → order
→ new bill in the books → compare promised vs actual
→ update price_history, supplier_scorecard, owner_prefs, savings → next sourcing round
```
1. **Promise vs reality:** quoted vs invoiced price; promised vs actual delivery; backorders → supplier reliability score → ranking penalizes unreliable suppliers.
2. **Owner decisions as signal:** rejections and edits become saved rules (e.g., reject plain buns twice → "seeded only").
3. **Price history:** detect creep ($6.29 quoted → $6.45 invoiced) → auto re-quote or alert; detect drops.
4. **Realized savings:** actual spend after switching vs old baseline ("saved $X since switching"); also checks whether ranking predictions were accurate.

**Demo simulation:** after approval, inject **one synthetic bill** (new supplier, $6.45 instead of $6.29, one day late) and show the loop react. **Disclose the injected bill.**

---

## 6. Sponsor tracks: exact requirements and how we meet them

Prize amounts are as listed by sponsors; "not listed" means we haven't found an amount.

### 6.1 CREAO (CORE): Best AI-First Disruptor, $2,000 + CREAO Launchpad
- **The one requirement:** at least one CREAO agent does the core work of the project. Build it on agent.creao.ai; in the Devpost README include a **share link to one of the agent's runs and the agent ID**; show the agent running in the demo.
- Other prize tracks (we target Disruptor): Best One-Person Company ($1,000), Best Team Empowerment ($500). Launchpad = 1 year of Pro Max, mentorship, CTO meet-and-greet, demo and launch support, interview opportunities.
- **Judging (all tracks):** AI as foundation **30%** ("remove the agent, does the product stop working?") · Depth on CREAO **25%** (extended with own tools/APIs, called from the product) · Originality and ambition **20%** · Track fit **15%** · Demo and storytelling **10%**.
- **Signup (upgraded hackathon account):** https://creao.ai/signup?hackathon=ucla-ai. Ask at their booth or Discord for more credits.
- **API Trigger:** base `https://agent.creao.ai`. **Update (Oct 16, checked against the current docs):** the routes below are outdated. Use `POST /api/v1/apps/{appId}/runs` with body `{"inputs": ...}` and poll `GET /api/v1/apps/{appId}/runs/{runId}`. See `agents/creao/README.md`.
  - Original notes:, header `Authorization: Bearer capi_…`
  - `POST /api/v1/runs` (body: `appId`, `inputs`, optional `webhookUrl`, `threadId`, `idempotencyKey` (8–128 chars), `chatModelId`; multipart for up to 10 files of 10 MB) → **202 + run id**
  - `POST /api/v1/apps/{appId}/runs` · `GET /api/v1/runs/{id}` (poll until completed/failed/cancelled) · `GET /api/v1/runs` · `POST /api/v1/runs/{id}/cancel`
  - Account key (Settings → API Keys, runs any agent) or agent key (agent page, that agent only). **Keep keys server-side.**
  - **Gotchas:** runs take **minutes**, with no token streaming. Webhooks are **unsigned** (verify by GET), may arrive duplicated (dedupe on runId), and **can't reach localhost**, so **we poll instead**. Packages installed in chat are saved only via **Create Agent / Update Environment**. Scheduled runs and workflows are supported. Every run spends credits; don't let an agent start itself.
  - Docs: https://docs.creao.ai/getting-started/quickstart · https://docs.creao.ai/pro/api-trigger · https://docs.creao.ai/features/scheduled-runs · https://docs.creao.ai/features/secrets
- **Our fit:** discovery and follow-up is core: without it, nobody knows which suppliers to contact. Pitch line: "Remove the agent, and the product stops working."

### 6.2 Browser Use (CORE), prize not listed
- Track text: "Build an AI agent with Browser Use that turns a goal into action on the web… an agent that pays and completes transactions for you… Show us a working demo and how your project delivers for its intended audience."
- **Our fit:** web agent turns "cheaper bun flats" into real cart totals with evidence. **We stop before checkout** (purchase only after approval, via the checkout agent, sandbox only).
- **Speed tips:** run sites in parallel; script known sites; prefer page text over screenshots where possible; go straight to search URLs; pre-run and cache before the demo.

### 6.3 Photon (CORE): Best Voice Agent, $300 cash + $100 Photon credits
- Other Photon prizes: iMessage Track Winner $700 ($400 cash + $300 credits + fast-track to final interview round), iMessage Runner-Up $300 ($200 cash + $100 credits). We target **Best Voice Agent**. iMessage is optional (the owner could text the agent).
- **Requirement:** project **built on Photon 2.0 and live on at least one Photon channel.** iMessage is the home channel for text agents; **voice agents live on an SMS number**.
- **Setup:** Photon 2.0 beta is free (no promo code): sign up at the dashboard (photon.co/beta), create a project, add channels. **Voice:** add an SMS number, set up a Voice profile, connect over **SIP** to Retell, **ElevenLabs**, LiveKit or Twilio. Calls and group chats on iMessage need a dedicated iMessage line, so **build voice on SMS**.
- Docs: Photon 2.0 beta docs (still being written) + cookbooks with step-by-step voice setup per provider; SDK reference: https://photon.codes/docs/spectrum-ts/introduction
- **Judging:** Vision (hybrid intelligence) · Craft (cohesive, responsive, tasteful) · Depth (real-world context, edge cases) · Traction (real users tomorrow).
- **What great looks like:** real human-in-the-loop collaboration; context that persists across time, channels and teammates; helps without demanding attention; thoughtful safety and fallbacks; **for voice: natural turn-taking, low latency, and remembering the call when the conversation moves back to text.**

### 6.4 Paper (CORE): Best Designed Hack, $1,500 / $1,000 / $500
- **Judging:** (1) design fundamentals (hierarchy, legibility, consistency, contrast) (2) ease of use (3) **personal craft and character: differentiate from clearly AI-generated designs.**
- **Setup:** download the Paper desktop app (agents connect to the canvas over MCP). Redeem **`LAHACKS26`** on a monthly plan to upgrade a team of four. **It auto-renews after one month**: cancel before then, or email team@paper.design proof of enrollment for a free year.
- **Workflow:** style guide on the canvas → turn the build into screens → "send it back to code."
- **Screens to design:** (1) Mission control (suppliers and status, live browser evidence, call transcript, quotes arriving); (2) Supplier detail (cart screenshot or transcript, normalized price, why flagged); (3) Savings and approval (+ savings-to-date, supplier scorecard).
- **Design guidance:** pick a real visual identity by hand. Avoid generic AI looks (gradient washes, default fonts like Inter/Roboto, emoji, left-border cards). Make the approval and trust information extremely clear.
- Page: https://paper.design/la-hacks

### 6.5 StepFun (CORE): "Get It Done with Step 5 Preview", prize not listed
- **Requirement:** "Your app or workflow **must call step-5-preview when it runs.** You can use other models, frameworks, and services too; explain what each does and how they work alongside Step 5 Preview." Judges look at **how well the project completes the task, how reliable the implementation is, and whether users can understand and control the workflow.** The prompt mentions "use image understanding to guide the next action."
- **Model facts (OpenRouter page):** StepFun flagship for agentic work; sparse MoE (27B active / 600B total); **1M context**; **$1.00 / 1M input, $2.70 / 1M output**; released **Oct 8, 2026**; OpenAI-compatible via OpenRouter, slug `stepfun/step-5-preview`. **UNCONFIRMED:** image input support and explicit tool-calling support; how teams get API access or credits at the event.
- **Our use:** orchestrator planning; `decide()`; quote-request drafting and reply parsing; approval-card reasons; screenshot reading if images work.
- **Writeup must include a "which model does what" section:** Step 5 Preview = main LLM on every run; ElevenLabs = voice; Jev = optional fast decisions.

### 6.6 Visa (PROPOSED), 1st place $2,000
- Challenge: "design and build a commerce experience powered by Generative AI that makes shopping more intuitive, personalized, and frictionless while enabling secure and trusted payments." Stages: discovery, decision-making, personalization, checkout, payments, loyalty/rewards, post-purchase.
- **Judging:** Innovation 25% · User Experience 25% · Technical Execution 20% · Commerce Impact 15% · Trust & Responsibility 15% (privacy, transparency, security, responsible AI).
- **Our fit:** "shopping for your business." **Add:** owner spending rules (per-order limit, approved-supplier list, monthly budget) + sandbox payment after approval (+ optional post-purchase tracking and reorder).
- **Risk:** the prompt is consumer-framed. Ask Visa early whether a small-business purchasing agent counts, and what payment sandbox or APIs they provide.

### 6.7 MLH Best Use of ElevenLabs (ADD-ON), wireless earbuds
- ElevenLabs powers the voice agent on supplier calls.

### 6.8 Sentry (ADD-ON): guaranteed interviews for every member of the winning team
- "Build with Sentry and use **at least two products beyond error monitoring**: Session Replay, Logs, Tracing, Profiling, Uptime Monitoring, or MCP/AI agent monitoring… Show us how observability actually shaped what you built."
- **Plan:** Session Replay (dashboard) + Tracing (backend, browser runs, calls); Logs if easy. Record one real bug or slow step it helped find.

### 6.9 Fetch.ai (DEFERRED: add at H18 only if the core run works)
- Challenge: "ASI:One Agent Challenge – From Intent to Action." Mandatory: (1) register agent on agentverse.ai, (2) show interaction from asi1.ai, (3) submit agent profiles, chats and project info through the **Submission Agent** (https://asi1.ai/festival/lahacksai2026/dashboard) plus Devpost.
- Basic requirements: at least one agent on Agentverse; implement the **Agent Chat Protocol**; discoverable and usable through ASI:One; meaningful tool execution or multi-agent orchestration; **complete the primary workflow entirely within an ASI:One conversation**; **public GitHub repo** with run instructions. Bonus: multi-agent collaboration, Payment Protocol, ASI Interactive Cards, reliability and error recovery, real-time data, agent that could keep running after the hackathon. They warn against "simple chatbots or thin wrappers."
- Hackpack: https://www.fetch.ai/events/hackathons/la-hacks-ai-hackathon-2026/hackpack · Submission process: https://docs.google.com/document/d/1UDW-X1C24hxZviFOQzjTeh0pXRNAoflMb8lhJqZP9Z0 · Resources: https://docs.google.com/document/d/10w0c9L1GL8FkeciTGyBzUCWUgrEsKJgZh4hxG_96XLs · Chat protocol: https://innovationlab.fetch.ai/resources/docs/agent-communication/agent-chat-protocol · Interactive cards: https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards · Payment protocol: https://innovationlab.fetch.ai/resources/docs/agent-transaction/agent-payment-protocol
- **Workshop notes:** `pip install uagents openai` (ASI:One models are OpenAI-compatible); keep the agent **seed constant** (it sets the address); **unique port** per agent; `mailbox=True`; run it, open the "Agent inspector" link while logged into Agentverse → Connect → Mailbox → "Agent successfully connected"; only the orchestrator needs the chat protocol; sub-agents need shared request/response models; an Agent README describes capabilities for discovery; mailbox agents only need to run during the demo; call agents by `@handle` in the demo.
- **Retrofit plan (2–4 hours):** wrap `start_job` / `get_status` / `approve` in a chat-protocol uAgent; immediate acknowledgement then async updates; approval as an interactive card (fallback: text "approve"); register; test via shared chat link. Optional: the earlier "demo supplier agent" idea (a fictional wholesaler agent quoting agent to agent) for a stronger Fetch story.
- **UNCONFIRMED:** prize for this event (April LA Hacks 2026 offered $2,500 / $1,500 / $1,000 + internship interviews).

### 6.10 Intuit / QuickBooks (OPTIONAL): $200 / $100 / $50 gift card per person
- "Reimagining Accounting with System One Models." Build something QuickBooks users couldn't get from classical ML or conventional GenAI. Principles: a **meaningful technical unlock** (not possible, or prohibitively slow, expensive or difficult before); **clear customer value**; **realistic evaluation**: teams define their own methodology and show evidence of beating classical ML or LLM approaches, not toy examples. A System One model isn't strictly required, but solutions should "embody the System One spirit" and, where possible, use a System-One-compatible API. Data modifications must be disclosed.
- **Jev (TypeSafe AI):** released Sep 15, 2026 (early access). Returns **typed answers from a fixed schema with calibrated 0–1 confidence**, not text; many questions answered in parallel in one call; claimed ~70–500 ms latency and $0.042 / 1M input tokens. **Claims are company-reported and not independently verified.**
- **If pursued:** Jev for routine `decide()` calls (same-product matching, page type, reply intent); evaluation on ~100–200 labelled listing-vs-item pairs comparing Jev vs Step 5 Preview vs string similarity on accuracy, calibration, latency and cost per 1,000 decisions. Story: "Jev = fast System 1 decisions, Step 5 Preview = System 2 reasoning." **Only if a Jev key arrives by about H4 and someone has 6–8 spare hours.**

---

## 7. Team split (current, Fetch deferred)

| Person | Owns | Key milestones |
|---|---|---|
| **P1 · Orchestrator, brain & data** | Orchestrator service and API; Step 5 Preview client and `decide()`; ranking engine; QuickBooks loader and baseline; feedback-loop tables; checkout agent (Visa, if confirmed); StepFun writeup section; Fetch retrofit at H18 if time | H1 contracts · H3 first step-5-preview call · H6 end-to-end on fake data · H14 ranking on real quotes + savings · H18 checkout/sandbox, feedback-loop simulation |
| **P2 · Web side** | CREAO discovery + scheduled follow-ups (API Trigger, polling); Browser Use web agent (parallel, scripted demo sites, screenshots, stop before checkout); cached runs | H1 pick 3–4 sites with public prices and no login · H6 one real cart total + CREAO supplier list · H10 three sites in parallel · H14 CREAO list drives browsing and P3's contacts · H18 cached runs · H22 CREAO agent ID + run link in README |
| **P3 · Voice & messaging** | Photon project, SMS number, Voice profile, ElevenLabs over SIP; call script (AI disclosure, one negotiation, never commit to buy); transcript → quote; SMS/email quote requests and replies → quotes; supplier role scripts for teammates | H2 Photon number live · H6 call to a teammate → quote saved · H10 text request + parsed reply · H14 negotiation uses the real best quote · H18 rehearsed |
| **P4 · Frontend & demo** | Paper setup and style guide (by hand); 3 screens; Next.js dashboard; FastAPI event server + SSE + fake event generator; Sentry (Replay + Tracing); demo script, backup video, Devpost | H1 Paper installed + promo + team invited · H3 style guide · H6 mission control on fake events · H10 detail + approval screens · H14 live events · H18 Sentry + polish · H20 backup video · H22 Devpost draft |

### Timeline and checkpoints (15-minute standups)
- **H0–1, all together:** pick the niche, 3–4 supplier sites and demo items; decide who plays suppliers; write the shared formats, events, `decide()` and orchestrator API; set up the repo and `.env`; create fixtures.
- **H1–6:** everyone builds alone against fake data. **H6 checkpoint:** request → fake quotes → ranking → approval card, end to end on the dashboard.
- **H6–14:** swap in real workers one at a time (H10 sync). **H14 checkpoint:** one complete real run. If not, **cut a channel; don't move the freeze.**
- **H14–18:** harden (retries, timeouts, caching), Sentry, design polish; stretch only if green (Jev, Visa extras).
- **H18: feature freeze.** Decide on the Fetch retrofit.
- **H18–24:** submissions (Devpost, CREAO run link + agent ID, Photon, StepFun model section, Fetch if done), backup video, rehearse the demo at least 3 times.

---

## 8. Guardrails and responsible-AI rules (non-negotiable)
- **Never complete a real purchase.** Stop before checkout; pay only in sandbox and only after explicit owner approval and within spending rules.
- **Never call real businesses during development or the demo.** Calls go to teammates acting as suppliers.
- The voice agent **always discloses it's an AI** calling on behalf of the business. **No recording without consent** (California requires all parties to consent).
- Keys live only in `.env` (gitignored). CREAO `capi_` keys stay server-side. Never put keys in frontend code or the public repo.
- Every quote carries **evidence** (URL, screenshot or transcript). Every recommendation shows **why**, the data used, and what the agent was unsure about.
- If nothing beats the baseline, say so.
- Label all simulated or synthetic data and cached replays in the demo and the submission (disclose dataset modifications).

---

## 9. Risks and pre-agreed fallbacks

| Risk | Early warning | Fallback | Owner |
|---|---|---|---|
| Supplier sites block the bot | CAPTCHA or login wall | Swap the site, use a scripted path, or play a cached run | P2 |
| Photon voice setup stalls | No working call by H8 | Text-only via Photon (still Photon-eligible, not voice prize) | P3 |
| CREAO slow or out of credits | > 3 min per run | Cache the discovery result; ask CREAO for credits | P2 |
| Step 5 Preview access or rate limits | Calls failing by H4 | Point `decide()` at another LLM to keep building; StepFun drops unless fixed | P1 |
| No Jev key | None by H4 | `decide()` stays on Step 5 Preview; Intuit dropped | P1 |
| Visa says consumer-only | Their answer at the event | Drop Visa; keep the cart handoff | P1/P4 |
| Integration breaks late | No full real run by H14 | Cut the weakest channel | All |
| Live demo fails | Flaky at H18 | Replay a recorded event log at real speed (say it's a replay) + backup video | P4 |

---

## 10. Open questions (settle Saturday morning)
- [ ] Exact hacking start/end → map H0–H24 to clock times.
- [ ] StepFun: prize, how to get API access or credits at the event, **does step-5-preview accept images?**, tool-calling support.
- [ ] Visa: does a small-business purchasing agent qualify? What payment sandbox or APIs do they provide?
- [ ] Browser Use: prize and any requirements beyond the tracks page.
- [ ] Fetch: this event's prize (decides whether the H18 retrofit is worth it).
- [ ] Jev API access this weekend (Intuit / TypeSafe booth).
- [ ] Can the ElevenLabs agent use a custom LLM (point it at Step 5 Preview)?
- [ ] Which 3–4 supplier sites show prices without a login? (Bakery packaging/disposables or bun suppliers.)
- [ ] Who plays the two suppliers on demo calls (one easy, one who pushes back)?
- [ ] Final product name.

---

## 11. Guidance for the coding agent: how to start
1. **Don't start feature code first.** Scaffold the repo layout in §4.9, then implement `shared/` (Pydantic models for §4.4–4.6, `decide()` stub, orchestrator API types) and `fixtures/` (fake quotes and events for the bakery job).
2. **Data loader:** parse company-001 `bills.yaml` → per-item baseline (avg, range, qty/order, cadence, current vendor and terms). Reproduce the numbers in §5.3 as a sanity check.
3. **Ranking engine:** pure, unit-tested functions: pack normalization, landed cost = (price × qty + shipping) / qty in comparable units, flags, savings per order and year vs baseline, scorecard and prefs adjustments.
4. **Orchestrator:** async job runner implementing `start_job` / `get_status` / `approve`, posting events; runs against fixture workers first.
5. **Event server + SSE** (FastAPI) and a fake event generator so the frontend can start immediately.
6. **Then the real workers**, one at a time, behind the same interfaces: CREAO client (polling), Browser Use web agent, Photon/ElevenLabs (likely TypeScript service), checkout sandbox.
7. **Feedback loop + demo simulation:** synthetic bill injection, price-creep detection, scorecard update, re-quote trigger.
8. Keep everything runnable with **fixtures only** (no external keys) so the demo can never fully fail.
9. Add Sentry early (tracing on orchestrator and workers).
10. Ask the team before anything irreversible or external: real calls, real payments, publishing keys, or contacting real businesses.

Suggested stack: Python 3.11+, FastAPI, Pydantic v2, SQLite (SQLModel or sqlite3), httpx/asyncio, Playwright + Browser Use, Node/TypeScript for Photon, Next.js/React for the dashboard, Sentry SDKs (Python + JS).

---

## 12. Reference links
- LA Hacks live site: https://live.lahacks.com/ · registration: https://ai.lahacks.com/
- QuickBooks dataset: https://huggingface.co/datasets/CL251215/QuickBooks-Hackathon-2026
- CREAO: https://creao.ai/signup?hackathon=ucla-ai · docs https://docs.creao.ai/
- Photon SDK: https://photon.codes/docs/spectrum-ts/introduction · beta: photon.co/beta
- Paper: https://paper.design/la-hacks · docs https://paper.design/docs · MCP https://paper.design/docs/mcp
- Step 5 Preview: https://openrouter.ai/stepfun/step-5-preview
- Fetch hackpack: https://www.fetch.ai/events/hackathons/la-hacks-ai-hackathon-2026/hackpack
- Jev / System One background: https://www.envisioning.com/vocab/system-one-model · https://levelup.gitconnected.com/what-is-jev-and-can-it-really-replace-llms-in-your-ai-stack-4d0b4e284ddd
- Visa, Browser Use, Sentry, MLH: requirements as quoted in §6 (from the LA Hacks tracks page and sponsor docs).

## 13. Related planning artifacts (made in the planning chat; private to the team owner)
- A design canvas, "Supplier Quote Agent – Architecture & Layout": product map, technical architecture, dashboard layout mockup. **Note:** older boards still show Fetch/iMessage as the owner channel; the "Product map" board is the most current but still lists Fetch as core.
- A doc, "Hackathon Work Split – Supplier Quote Agent." **Note:** written before Fetch was deferred; this file supersedes it.
