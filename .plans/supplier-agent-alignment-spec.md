# Supplier Agent: Team Alignment Spec (v2)

> v1 was agreed in the team interview. v2 adds the fixes from `.plans/architecture-review.md` (C1–C8, M1–M8) and a new reliability section (§10).
> Document order of priority:
> 1. This spec: the decisions, the pipeline and the reliability rules.
> 2. `docs/DATA_FLOW.md`: the data contracts.
> 3. `docs/PROJECT_BRIEF.md`: background only.
>
> Product name: **not chosen.** The team picks it at H0.
> Labels: **[Verified]** = vendor docs confirm it. **[Unverified]** = test it at the event.

---

## 1. Summary

A small-business owner connects their books. The product shows what they spent on supplies and prefills a shopping list from the past bills.
CREAO finds suppliers. Then **one browser session for each supplier** searches the site, builds the real cart and reads the true totals. Jev makes the fast judgments inside each session, and plain code does all the arithmetic.
At the same time, the product texts and then calls the owner's current distributor. It asks the distributor to beat the best plan so far. The ranking updates live during the call.
The owner approves a plan that the spending rules already passed. The backend mints a simulated single-use card for each supplier. Each session goes to its checkout, and **code stops it at the payment step.**

---

## 2. Decisions

| Topic | Decision |
|---|---|
| Core flow | A multi-item list with split carts, across real supplier sites. |
| Demo story | Hearthloaf Bakeries (company-001) buys its non-food supplies online. |
| Data | Inject 10–20 synthetic supply bills from a fictional local distributor, and disclose them. company-001 has no supply bills of its own. |
| Persona | Owner, first-time setup. |
| Screens | 0 Spend insights → 1 Shopping list → 2 Working → 3 Results + approval → 4 Checkout handoff. |
| Real sites | 3 sites. Choose them at H0 after the preflight (§10.7). No test store. |
| **Browser sessions** | **One long-lived session for each supplier** does the search, the full cart, the subset totals and the checkout. *(C3)* |
| **Browser stack** | The **open-source `browser_use.Agent`** in all environments: a local browser in development, `Browser(use_cloud=True)` in the demo. The hosted v3 API accepts only `bu-mini`/`bu-max` **[Verified]**, so we cannot use it with Step 5 Preview. *(C2)* |
| **Speed** | Each demo site gets a **scripted adapter** (Playwright steps + Jev decisions). The LLM agent is the fallback, and it runs one "unknown site" tile. *(C1)* |
| Step 5 Preview | (1) Search specs, (2) browser-agent LLM (H3 gate), (3) SMS/email drafts, (4) escalation in `decide()`, (5) the results summary. |
| **Jev** | The judgments J1–J10 (§6). Jev never does arithmetic, writes text or reads images. |
| CREAO | Supplier discovery (8–10 candidates with contacts). Jev filters them. The result is cached by category + ZIP. *(M3)* |
| **Distributor conversation** | **Call first** (default, `DISTRIBUTOR_FLOW=call_first`): the agent calls the distributor as soon as the first plan exists, then texts a recap. The older SMS-first path (`sms_first`) stays available. *(M2, changed Oct 17)* |
| Voice | Photon SMS number + Voice profile → SIP → ElevenLabs. A teammate plays the distributor. **This is the wow moment.** |
| Off-web quotes | Status `quoted`. Per-item prices if the distributor gives them. Otherwise a `bundleTotal` plan. *(C5)* A quoted plan can win only when the owner turns on "Accept quoted offer". |
| **Spending rules** | Checked **before approval**. Each plan shows a badge. The cards are minted at approval. *(C8)* |
| **Payment stop** | **Code-enforced:** a Jev page-state guard + a DOM card-field check. The browser never gets card data. *(C7)* |
| Execute | Guest checkout with a team-controlled address and email. The session stops at the payment step. The UI shows a placeholder "Place order" button. |
| Visa | Spending rules + a simulated agent-scoped single-use card for each supplier. |
| Owner controls | Approve, Reject, and "Accept quoted offer". |
| Look | Bright utility: light, high contrast, bold status colors. |
| Demo | 3 minutes. The call is the wow moment. Live, with a replay switch. |
| Tracks | Browser Use, StepFun, CREAO, Photon (voice), MLH ElevenLabs, Paper, Visa, Intuit (with the evaluation), Sentry (Replay + Tracing + AI monitoring). **Fetch.ai is dropped.** |
| **Restock planner** | A forecaster decides when and how much to order. Its feedback loop is guided by Jev (J11 miss causes, J12 owner messages). The Intuit evaluation uses this loop. See §13. A CREAO scheduled run triggers the daily check. |
| Team | 2 people on agents (paired), 1 on backend, 1 on frontend. |
| Cut order | Decide at H14. If the backend is behind, the Visa card is its first cut. |

---

## 3. Screens

Status colors are the same on every screen: **verified** (green), **quoted** (amber), **estimated** (blue-grey), **blocked/failed** (red).

| # | Screen | Content | Actions |
|---|---|---|---|
| 0 | Spend insights | Supply spend last year, the top items with past unit prices and cadence. The bill lines that J1 sorted into groups. Badge: "from your books (supply bills are synthetic)". | **Find cheaper** |
| 1 | Shopping list | The prefilled items, the spec chips, the suppliers from CREAO ("found 9, using 3"), the delivery ZIP code. | **Compare suppliers** |
| 2 | Working | One tile for each supplier lane (live view or a stream of screenshots) with the lane status. The distributor thread: SMS, then the live call transcript. "Best plan so far" updates on each event. Each lane shows its own failure, and the other lanes continue. | (watch) |
| 3 | Results + approval | The recommended plan, the savings vs past spend (this order + yearly), the carts with screenshots and a totals check, the other plans, the quoted offer + transcript, and Jev-backed warnings. A **rules badge** on each plan. | **Approve**, **Reject**, **Accept quoted offer** |
| 4 | Checkout handoff | For each supplier: the live view (or the last screenshot) at the payment step, and the simulated card. A placeholder **Place order**. | none |

---

## 4. Pipeline

```
Screen 0 opens
 ├─ load books → J1 triage → insights                          [code + Jev]
 └─ CREAO discovery (cache: category+ZIP) → J3 filter           [CREAO + Jev]

"Compare suppliers"  (job deadline starts: T0)
 ├─ Step 5 Preview: one spec per item (parallel)
 ├─ Supplier lane ×3 (parallel, ONE session each)
 │    search → DOM results → J4 → open top-k → J5 → add matched items
 │    → cart → J7 totals + code sum check → supplier_cart_ready
 │    (J6 + DOM check after every navigation; code-enforced stop)
 ├─ Distributor lane
 │    T0: SMS request (Step 5 draft) → J8 on each reply
 │    no quote by T0+20 s → CALL when the first plan exists (at the latest T0+60 s)
 │    the call opens with the best plan so far
 │    J9 on each supplier line → call_offer → rerank
 │    after the call: SMS summary
 └─ Optimizer runs on every supplier_cart_ready / quote event      [code]
       → for the top 2 split plans: the lanes remove items, re-read the totals (J7)

Results at T0+150 s, or earlier when all lanes are done:
   code ranks · rules check · Step 5 summary · warnings from J5/J10
Approve → mint simulated cards → each lane sets its cart to the plan subset → checkout
   → J6 = shipping step → fill the team address → J6 = payment step → STOP → live view
```

### 4.1 Stage details

| Stage | Type | Rule |
|---|---|---|
| Load books + triage | code + J1 | One Jev request for each distinct item. Cache the results. |
| Discovery | CREAO + J3 | Start when screen 0 loads. Poll every 10 s, and wait at most 5 min. Use the cache when it exists. Show the cache age. |
| Specs | Step 5 Preview | Temperature 0. Cache by input hash. |
| Supplier lane | scripted adapter (or LLM agent) + J4–J7 | Budgets: search 45 s, cart 45 s, each subset 20 s. A lane that goes over its budget reports `failed(timeout)`, and the job continues. |
| Optimizer | code | For each (item, supplier), keep the cheapest matched option. List every assignment (≤ 4⁸). Estimate the shipping only for a supplier that has no verified cart yet. *(M1)* |
| Subset totals | lane + J7 | Only for the top 2 split plans, and only before the deadline. |
| Distributor | Photon + ElevenLabs + J8/J9 | The call asks for prices item by item. It never commits to buy. It says that it is an AI. |
| Rank + rules | code | Rank by verified total. A plan can be "recommended" only when all its carts are verified, or when its quote is quoted **and** the owner accepted quoted offers. |
| Summary | Step 5 Preview | One paragraph. The warnings come from code templates, not from the LLM. *(M4)* |
| Cards | code | One card for each supplier in the approved plan. Limit = the verified total × 1.02. Merchant-locked. Expires in 60 min. Labelled "simulated". |
| Handoff | lane | Set the cart to the plan subset, then go to checkout. Stop at the payment step. |

---

## 5. Browser lane design

**Stack:**
- `browser_use.Agent` and `Browser`.
- Model: `ChatOpenAI(base_url=OpenRouter, model="stepfun/step-5-preview")` after the H3 gate. Fallback: a proven model.
- Custom tools (`@tools.action`, **[Verified]** supported): `rank_results` (J4), `judge_listing` (J5), `page_state` (J6), `read_cart_totals` (J7). Each tool calls the backend's Jev client.

**Adapters:** each demo site has a `SiteAdapter` with these parts:
- `search_url(query)`
- `result_selector`
- `product_extract()`
- `add_to_cart(qty)`
- `cart_url`
- `remove_item(sku)`
- `checkout_entry`

An adapter calls the same Jev functions as the LLM tools. When an adapter step fails, that lane falls back to the LLM agent for the rest of the job.

**The lane state machine:**
```
queued → searching → carting → cart_ready → (subset_pricing ↔ cart_ready)* → idle
idle → checkout → at_payment            (after approval)
any → failed(reason) | blocked(login|bot_check|out_of_stock)
```

**The payment guard** runs after every navigation and before every type or click action:
1. A DOM check: the page has an input with `autocomplete` in {cc-number, cc-exp, cc-csc}, or a name or id that matches `/card|cvv|cvc/i`. If yes → stop.
2. J6 `page_state`: if P(payment step) ≥ 0.2 → stop.
3. If J6 times out or fails, **stop.** The guard fails closed.

Neither the browser nor the agent prompt ever holds card data.

---

## 6. Jev decision points

| ID | Decision | Primitive(s) | Used by | When unsure |
|---|---|---|---|---|
| J1 | Bill-line triage | Choice `category`; Noul "online-buyable commodity" | Insights | Show as "uncategorized" |
| J3 | Supplier filter | Nouls: sells category · prices without login · is a supplier · ships to businesses | Lane selection | Drop the candidate |
| J4 | Search-result rerank | One Score for each result (different / related / same) | Lane | Open the top 3, not the top 1 |
| J5 | Listing judge | Score match; a Noul for each `mustHave`; Choice `packCount` (regex candidates + "not stated"); Noul in stock | Offers, warnings, ranking | Below the act band → escalate to Step 5; still unsure → flag `low_match`. Each doubt flag adds a ranking penalty of 5% of the line cost (`risk_penalty`), never a price |
| J6 | Page state (**safety**) | Choice of 9 page types | Payment guard, fallback | **Fail closed: stop** |
| J7 | Cart totals | 4 Choices over the regex money candidates (+ "none") | Verified totals | A failed code sum check → `totalsCheck: mismatch` → not verified |
| J8 | Reply intent + price | Choice intent; Choice for each item price | Distributor thread | Escalate to Step 5 |
| J9 | Live call line | Noul "new price or offer"; Choice "which item or the total"; Noul "agrees to beat" | Live rerank | Show the price as "heard, not confirmed" |
| J10 | Quote risk terms | Nouls: conditional price · delivery fee · availability risk | Warnings, reliability | Show a warning |

**Rules:**
- Jev gets **only** filtered text: DOM extracts, never full pages, and never images. **[Verified]** The jev-1.13 weak points are long, irrelevant state, images, math and dates.
- Code finds the candidate numbers with regex. Jev **selects** one, and code computes the result.
- For Choices, put the most likely option last, because Jev favors the first option. **[Verified]** Test the stability of the answers when the order is shuffled.
- Each decision has its own thresholds. Set them on cached runs at H10 and write them here.

**`decide(questions, state, stakes)`:**
1. Get the Jev answer.
2. If `stakes = safety`, never escalate. When unsure, take the safe branch.
3. If the answer is in the act band, return it.
4. If it is in the confirm band, ask Step 5 Preview the same question with the same state (JSON output).
5. Log both answers, the latency and the tokens. This log is the Intuit evaluation data.

---

## 7. Spending rules (Visa)

These rules run in the **rank stage**, on every plan:

| Rule | Default (from the books) |
|---|---|
| Per-order cap | 1.5 × the largest past supply order |
| Approved suppliers | The J3-approved discovery list + the current distributor (shown to the owner) |
| Monthly supply budget | The average monthly supply spend × 1.2, minus the spend so far this month |

- A plan that fails a rule shows the failed rule and cannot be approved.
- At approval, the backend checks the rules again (the values can change) and then mints the cards.

---

## 8. Guardrails

- **No payment is ever submitted** on a real site. The code guard in §5 enforces this. The "Place order" button is a placeholder.
- Guest checkout uses a **team-controlled address, email and phone** (the Photon number). Never use a third party's data.
- The voice agent says that it is an AI, never agrees to buy, and does not record without consent. It calls only teammates.
- SMS and email go only to team numbers and our auto-replier.
- Every price has evidence (a screenshot, a DOM extract, a transcript or a message) under `/evidence/{jobId}/`.
- Label all synthetic data: the injected bills, the simulated card, and the replay when it is used.
- Keys stay server-side. Only the webhook routes go through the tunnel, and each one checks a shared secret.

---

## 9. Team

| Role | Owns |
|---|---|
| **Agents (2, paired)** | Site adapters + the LLM-agent fallback · custom Jev tools in the lanes · the payment guard · cloud browsers and live view · CREAO agent + client · Photon number, Voice profile, SIP to ElevenLabs, call script · SMS thread + auto-replier · webhook tunnel |
| **Backend (1)** | Orchestrator, job and lane state machines, jobs API, SSE with replay · `shared/jev.py` + `decide()` · normalize, optimize (incremental), rank, rules, cards · data loader, injected bills, insights · evidence store · event log + replay · preflight endpoint · Intuit evaluation · Sentry tracing + AI monitoring |
| **Frontend (1)** | Paper style guide + 5 screens · Next.js app, SSE client with reconnect · lane tiles (live view / screenshot fallback) · call panel · admin drawer (mode switches, preflight results) · Sentry Replay · demo script, backup video, Devpost |

---

## 10. Reliability: production behavior and demo safety

### 10.1 Principles
1. **Fail closed on safety, degrade gracefully on features.** The payment guard stops when in doubt. Every other failure removes one supplier, one channel or one decision, and the job still ends with results.
2. **Lanes are isolated.** One lane's failure never stops the job or another lane. Each lane runs in its own task with its own budget.
3. **Every external call has a timeout, bounded retries, a fallback and a failure event.** No call waits without a limit.
4. **The job has a deadline.** At T0+150 s, the results come from whatever exists. Events that arrive later update the results, with the label "updated".
5. **The same input gives the same output.** Step 5 Preview runs at temperature 0, and Step 5 and Jev answers are cached by an input hash. Fixtures and replays are then exact.
6. **The UI never depends on one stream.** SSE reconnects with `Last-Event-ID`, and the server sends again from the event log. `GET /jobs/{id}` always returns the full current state.

### 10.2 Dependency table

| Dependency | Timeout | Retries | When it fails | Failure is visible as |
|---|---|---|---|---|
| Jev (non-safety) | 5 s | SDK default on 429/529, max 2 | Escalate to Step 5; then mark "unsure" | Warning on the item |
| Jev (J6 safety) | 3 s | 1 | **Stop the lane** | Lane: `failed(guard_unavailable)` |
| Step 5 Preview (OpenRouter) | 30 s | 2, backoff | Specs: a template spec from the bill line. Summary: a template. Browser LLM: the fallback model | "Summary unavailable" text |
| CREAO | 5 min total, poll every 10 s | none (one run) | Use the cache. With no cache, use the pre-approved site list | "Suppliers from cache (age)" |
| Browser lane (search / cart / subset) | 45 s / 45 s / 20 s | 1 retry of the failed step | Adapter → LLM agent → lane `failed` | Red tile with the reason |
| Cloud browser start | 20 s | 1 | Local browser (no live view) | Screenshot stream in the tile |
| Photon SMS | 10 s per send | 2 | Email | Thread shows "email sent" |
| Call connect | 20 s to answer | 1 redial | SMS-only quote | Call panel: "no answer" |
| ElevenLabs transcript stream | n/a | auto | Use the post-call transcript | Transcript appears after the call |
| Sentry | async | none | Drop the events | none (never blocks) |

### 10.3 Concurrency and cost limits
- Jev: a semaphore of 6 requests at a time (the cookbook saw rate limits above about 8). Check the console limits at H4.
- OpenRouter: 4 at a time. `max_tokens` is set for each call type.
- Browser lanes: at most 4 (3 sites + 1 unknown-site tile).
- Browser Use cost cap for each agent run. OpenRouter credit alarm.

### 10.4 Idempotency and state
- SQLite in WAL mode. Tables: `jobs`, `lanes`, `events` (with a sequence number), `quotes`, `carts`, `cards`, `decisions` (the `decide()` log), `cache`.
- `POST /jobs` takes an `Idempotency-Key`. `approve` can be repeated safely: a second call returns the first result.
- CREAO runs use `idempotencyKey = hash(category, zip, date)`.
- Webhook handlers remove duplicate events by provider event id.

**The job state machine:**
```
created → discovering → gathering → results_ready → approved → handing_off → at_payment
                                   ↘ rejected                ↘ partial_handoff
any → failed(reason)
```

### 10.5 Run modes
Each dependency has a mode switch: `live | cached | replay`. Set it in the env or in the admin drawer.
- `cached`: use the recorded responses for that dependency (for example, CREAO=cached while the browsers are live).
- `replay`: play the whole job's event log at real speed. The UI shows a small "replay" label. The narrator says so.

The fixture mode (all dependencies cached) runs with **no keys**. CI and the H6 checkpoint use it.

### 10.6 Tests
| Level | What | When |
|---|---|---|
| Unit | normalize, optimize (incl. free-shipping edge cases), rules, cards, J7 sum check | Before H6 |
| Golden | J5 and J7 on saved HTML from each demo site (expected answers fixed) | H10, and after every adapter change |
| Contract | Pydantic models = generated TypeScript types; fixture events validate | CI |
| End-to-end | The fixture mode from `POST /jobs` to `at_payment` | CI + every checkpoint |
| **Failure drills** | Kill one lane · Jev down · OpenRouter down · CREAO slow · call not answered · SSE drop · session expiry. Each one must end as the table in §10.2 says | H16 |
| Live canary | One search on each demo site | In the preflight |

### 10.7 Preflight (`GET /health/preflight`, shown in the admin drawer)
Run it 15 minutes before judging, and again 2 minutes before:
1. All keys load. The tunnel URL answers its own webhook route.
2. Jev ping: p50 latency over 5 calls.
3. OpenRouter `step-5-preview` ping.
4. Cloud browser: create a session, get the live URL, close the session.
5. Canary on each demo site: search one item, with no login wall and no bot check (J6).
6. Photon number status, and a test SMS to the team phone.
7. CREAO cache age.
8. Free disk space for the evidence files.

Each check shows green, amber or red. A red check sets that dependency to `cached` mode.

### 10.8 Demo runbook
1. **T−15 min:** run the preflight. Run one full warm-up job, which also fills the caches. Record its event log as the replay backup.
2. **T−5 min:** create the 3 cloud browser sessions, so that they are warm when the demo starts. Keep them alive.
3. **Live:** the narrator starts the job on screen 1 at about 0:20 in the script. Screen 2 must show "best plan so far" before the call starts at about 1:20.
4. **If a lane fails live:** say "one supplier blocked us; the others continue". This is the designed behavior.
5. **If the whole run stalls for more than 20 s:** turn on replay from the admin drawer and say so.
6. **Backup:** the recorded video (made at H18–H20).

### 10.9 What changes for real production (out of scope for the demo)
- Payment: a real tokenized card per order (for example, a card-issuing API) entered through a human-confirmed step, or supplier B2B ordering APIs. No raw card numbers in an agent.
- Each customer gets a separate workspace, encrypted credentials and an audit log.
- Respect each supplier site's terms. Prefer supplier APIs or punch-out catalogs where they exist. Set rate limits for each site.
- A queue (for example, Redis + workers) in place of in-process tasks. Postgres in place of SQLite.
- Thresholds for Jev decisions are tuned on real labelled data and monitored for drift.

---

## 11. Demo script (3 minutes)

1. **(0:00) Insights.** "Hearthloaf spent $X on supplies last year" (the supply bills are synthetic). Click **Find cheaper**.
2. **(0:20) List.** The list is prefilled. The spec chips show what the agent looks for. CREAO found the suppliers. Click **Compare suppliers**. The job starts.
3. **(0:30) Working.** Three live lanes search and build carts. The distributor gets an SMS. "Best plan so far" appears.
4. **(1:20) The call.** The distributor does not reply to the SMS, so the agent calls. It says that it is an AI and gives the best plan total. The teammate counters, and the ranking updates live.
5. **(2:10) Results.** The split plan vs the quoted offer: savings vs past spend, a yearly projection, a totals check on each cart, and one warning. The rules badge is green.
6. **(2:35) Approve.** Simulated cards are minted. Each lane goes to checkout and stops at payment. "One click would place the order. Our code makes sure that no agent can."

---

## 12. Timeline checkpoints

| Hour | Checkpoint |
|---|---|
| H0–1 | Name. Site preflight → choose 3. Tunnel up. Visa question. Judging slot length. Freeze the contracts. Fixture mode runs end to end on stubs. |
| **H3** | Gate: step-5-preview drives the open-source agent to a guest cart on one real site. Confirm the live view URL from the `use_cloud` browser. |
| H4 | Jev key, rate limits, p50 latency. If there is no Jev key, `decide()` uses Step 5 only, and the evaluation compares Step 5 with string similarity. |
| H6 | Fixture end-to-end on the real UI. Unit tests green. |
| H10 | One real lane: search → cart → J7 verified. Golden tests for that site. One real call creates a quote. Time the segments. |
| **H14** | One full real run under 150 s. Decide the cut order. |
| H16 | Failure drills (§10.6). |
| H18 | Feature freeze. Record the replay log and the backup video. |
| H18–24 | Intuit evaluation, Devpost, CREAO run link + agent ID, StepFun section, rehearse 3 times with the preflight. |

---

## 13. Restock planner (forecaster) with a Jev-guided feedback loop

 This replaces the "saved quote history" feedback loop of the brief (§5.5).
> The planner decides **when** to order and **how much**. The sourcing pipeline (§4) decides **where**.

### 13.1 What the data supports (checked on company-001)
- 1,574 sales invoices with item lines (6 products), 40 customers, 3,046 customer payments, 3,606 bank rows.
- **Demand changes a lot:**
  - Bun sales peak at about 1,500–1,800 flats a week (weeks 9–14), then fall to about 100–330.
  - A new product launches about every 7 weeks (weeks 15, 22, 31, 38, 46).
  - The data covers **one year only, so no seasonality can be learned.**
- **Signals that code can detect:**
  - Customers who stop ordering (for example, one stops after week 30, one after week 40).
  - New customers.
  - Product launches.
  - **27 bills with "Partial backorder noted"**.
- **Signals that do not exist:**
  - Payroll has no hours.
  - Invoice memos are generic.
  - Nothing records real supply use.
  - Nothing records stock counts or spoilage.
- **Cash never limits this company** (lowest balance $86,802). The cash check stays in the design, but it never binds here.

### 13.2 Pipeline
```
real invoices → weekly product sales (code)
  → damped-trend forecast per product + range (code)
  → supply needs = Σ product forecast × usage ratio (code; owner-set ratios, labelled "assumed")
  → stock position (synthetic supply receipts − derived use) → order date + order-up-to quantity (code)
  → POST /planner/check (daily; CREAO scheduled run) → sourcing job with those quantities (§4)
Feedback, every week and on every owner message:
  actual vs forecast → miss? → code lists the facts → J11 cause → code applies that cause's update rule
  owner SMS ("wedding Saturday, 300 extra buns") → J12 typed adjustment → code applies it
  unsure (confirm band) → one SMS question to the owner → the answer is applied and saved as a label
```

### 13.3 Jev decision points (new)
| ID | Decision | Primitive(s) | State (filtered) | Code uses it for | When unsure |
|---|---|---|---|---|---|
| **J11** | Forecast-miss cause | Choice: one_time_event · lasting_shift_up · lasting_shift_down · customer_lost · customer_gained · supply_shortage · product_substitution · noise | item, week, the miss as a word bucket ("far below range"), code-detected facts (lost/new customers, launches, backorder memos, owner notes) | The update rule (table 13.4) | Ask the owner (SMS). Until then: no change |
| **J12** | Owner message → adjustment | Choice item (catalog + none) · Choice kind (one_time_extra · lasting_increase · lasting_decrease · discontinue · pause · not_a_forecast_change) · Choice quantity among regex candidates · Choice date phrase among regex candidates | The message text + candidates | A typed `ForecastAdjustment` | Confirm by SMS before applying |
| J13 (stretch) | New bill vs our order | Score match per line pair · Choice discrepancy | Bill line + order lines | Received quantity, supplier score | Flag for the owner |

Jev never calculates numbers. Code turns each number into a word bucket before Jev sees it, and code resolves all dates (TypeSafe date-extraction pattern).

### 13.4 Update rule for each J11 cause (code)
| Cause | Update |
|---|---|
| one_time_event | Replace that week with the forecast in the clean history (the week does not teach the model). |
| lasting_shift_up / lasting_shift_down | Reset the level to the mean of the last 2 weeks, and set the trend to 0. |
| customer_lost | Remove that customer's past orders for the item from the clean history. |
| customer_gained | Reset the level, the same as a lasting shift. |
| product_substitution | Same as lasting_shift_down for the declining item. |
| supply_shortage | Do not change demand. Add 1 week to that supplier's lead time for the safety stock. |
| noise | No change. |

### 13.5 Ordering rule (code)
- A weekly review with a delivery time of L weeks.
- **Order-up-to level** S = forecast(L + 1) + z·σ·√(L + 1), with z = 1.65 (about 95% service).
- **Order quantity** = S − (stock on hand + stock on order). The optimizer rounds it to packs, as today.
- **Order date** = the week in which the projected stock falls below the safety stock plus the demand during delivery, minus the delivery time.

### 13.6 Evaluation (Intuit submission; replaces the Jev-vs-Step-5 listing evaluation)
- **Replay the year, week by week, with three variants:**
  - **A:** a plain forecaster.
  - **B:** fixed rules (a large miss always resets the level).
  - **C:** J11 causes, with Step 5 Preview as the fallback.
- **Report:** WAPE (weighted absolute percentage error), bias, range coverage, simulated weeks without stock, and average stock held.
- **J12:** test it on about 100 written owner messages with known answers. Compare Jev, Step 5 Preview and keyword rules on accuracy, calibration and latency.
- **Honesty:**
  - The data is simulated.
  - The J11 labels are rule-derived weak labels.
  - Variant C in fixture mode uses stub rules. Only a live Jev run gives Jev results.

### 13.7 Data changes (disclose)
- The synthetic supply bills are **regenerated from the real sales** through the usage ratios. They are no longer flat 21-day orders.
- A compact derived file (`backend/fixtures/company-001-sales.json`: weekly sales, customer-weeks, bill memos) lets fixture mode and the tests run without the 400 MB dataset.

## 14. Open items

1. Live view URL from `Browser(use_cloud=True)` **[Unverified]**. Fallback: a screenshot stream.
2. A paid Browser Use key. The free cloud session limit is 15 min **[Verified]**, and screen 4 needs a longer session.
3. Photon and ElevenLabs: webhooks through the tunnel, or polling.
4. Jev: key, rate limits, latency (H4).
5. The team address, email and phone for guest checkout.
6. The thresholds for J4, J5, J6 and J8 (H10, write them in §6).
7. The injected supply items, which must exist on the chosen sites. The distributor's name and price level.
8. Visa: B2B eligibility and any real sandbox.
