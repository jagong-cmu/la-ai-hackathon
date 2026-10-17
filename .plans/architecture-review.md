# Architecture and Workflow Review

> Reviewed: `.plans/supplier-agent-alignment-spec.md`, `docs/DATA_FLOW.md`, `docs/PROJECT_BRIEF.md`, `docs/system-map.html`.
> Sources checked on Oct 17, 2026:
> - TypeSafe docs: API, models, confidence, images, jev-1.13 known weak points, entity-alignment cookbook.
> - Browser Use docs (through Context7): custom tools, cloud browsers, sessions, the v3 API.
> Labels: **[Verified]** = the docs say it. **[Unverified]** = vendor claim or our guess. Test it at the event.

---

## 0. Verdict

The product logic is sound. The main idea is to compare real landed totals after the cart, not list prices, and to keep the arithmetic in code. That is the right core.

The plan as written has **4 problems that would break the live demo**:

1. **The pipeline is too slow for a 3-minute demo.** The call waits for all cart checks, and the carts wait for all searches. A live run takes about 8–12 minutes. (§1, C1)
2. **Step 5 Preview cannot drive Browser Use Cloud's hosted agent.** The hosted API accepts only Browser Use's own models. (C2)
3. **The carts are lost between the stages.** Stages 4a, 7 and 12 use separate browser sessions, so the guest carts do not survive to the checkout handoff. (C3)
4. **Two critical safety and accuracy jobs depend on an LLM's self-report:**
   - "Stop before payment" is only a prompt instruction to an agent that reads untrusted web pages.
   - The agent reports its own match confidence and does its own unit conversion.

   **Jev is the right tool for both jobs.** (C4, C7)

The fix is one structural change and a set of Jev calls:

- **Structural change:** give each supplier **one long-lived browser session** that searches, builds the cart, checks the totals and does the checkout handoff.
- **Jev calls:** add **Jev decision points inside that session**. They choose the search results, judge each listing, pick the pack size from candidates that code finds, classify each page, and read the cart totals. Code does all the arithmetic and enforces the payment stop.

---

## 1. Critical issues

### C1. The live time does not fit the demo
The current chain is: CREAO (minutes) → specs → search ×3 → normalize → optimize → verify carts → call → rank.
Browser Use LLM agents take about 1–3 minutes for each search and 1–2 minutes for each cart [Unverified]. The call cannot start until stage 7 ends, so the wow moment comes 6–10 minutes into the run.

**Fix:**
- **Work per supplier.** Each supplier lane starts its cart as soon as its search ends. The optimizer runs again each time a lane reports, so "best plan so far" updates live.
- **Start the call earlier.** The call starts from the best plan so far ("about $X"), not from the final verified total. It runs in parallel with the cart checks.
- **Use fast paths.** The 3 demo sites get scripted Playwright steps with Jev decisions (§3). The LLM agent is the fallback for other sites.
- **Pre-run CREAO.** Cache the discovery result for each item category and ZIP code. CREAO still runs live, but the demo does not wait for it.

### C2. Step 5 Preview and Browser Use Cloud
- **[Verified]** The hosted API `POST /api/v3/run` accepts only `bu-mini` and `bu-max`.
- **[Verified]** The open-source `Agent` accepts any OpenAI-compatible model (`ChatOpenAI(base_url=…)`). It can also run in a cloud browser (`Browser(use_cloud=True)`).
- **Fix:** use the open-source agent in all environments:
  - Development: a local browser.
  - Demo: `Browser(use_cloud=True, cloud_proxy_country_code="us", cloud_timeout=…)`.
  - Model: `ChatOpenAI(base_url=OpenRouter, model="stepfun/step-5-preview")`. This is the H3 gate.
- **[Unverified]** that this route gives a **live view URL** for the screen 2 grid and the screen 4 handoff. Test it at H0. Fallback: show a stream of screenshots in each tile.
- **[Verified]** Cloud browser timeout: up to 15 minutes on the free plan and up to 240 minutes on paid plans. Screen 4 needs the session to stay open after the owner approves, so get a paid key or keep the demo short.

### C3. The session model loses the carts
The plan uses one agent per search (4a), one agent per unique cart (7) and a new session for the handoff (12). Guest carts live in one browser's cookies, so each new session starts with an empty cart.

**Fix:** use **one session per supplier for the whole job.** Each session does these steps in order:
1. Search.
2. Add all the matched items to the cart. This gives the "everything from X" total, and it shows the real shipping and the free-shipping threshold.
3. Read the totals.
4. For the top plans, remove items and read the subset totals in the same cart.
5. After approval, set the cart to the approved subset and go to checkout.

This uses fewer sessions and no rebuilds. Screen 4 shows the same browser that did the work. `cartKey` stays as the key for each cart state, and `sessionId` becomes required.

### C4. LLM self-reports where Jev should decide
In the current contract, the browser agent returns `matchConfidence` and converts `unitsPerPack` to the requested unit. These problems follow:
- The LLM's self-reported confidence is not calibrated.
- Unit conversion is arithmetic, and neither LLMs nor Jev do arithmetic reliably. **[Verified]** jev-1.13 weak point: "Jev is not a calculator."

**Fix:** follow the TypeSafe "select instead of generate" pattern:
1. **Code** finds the candidate numbers with regex. Examples: "100 Count", "Case of 1,000", "10/100", "$24.99".
2. **Jev** chooses which candidate is the pack count, the unit or the price.
3. **Code** does all the math.

Jev's match judgment uses the entity-alignment cookbook design (§3, J5).

### C5. A phone quote is a bundle, but the contract is per item
The distributor answers the full list with one total ("$210 delivered"). `SupplierOffers` expects a price for each item.

**Fix:** support both:
- The call script asks for prices item by item first.
- If the distributor gives only a total, the quote becomes a **single-supplier quoted plan** with `bundleTotal`. It skips normalize and optimize, and it is compared at the results stage.

### C6. Inbound webhooks need a public URL
These inbound paths need a public URL:
- Photon inbound SMS and call events.
- ElevenLabs post-call transcripts.
- CREAO webhooks. We already chose to poll CREAO, so they are not needed.

The backend runs on a laptop.

**Fix:** start a `cloudflared` tunnel (or deploy the backend) at H0, and register its URL. Check whether Photon and ElevenLabs also allow polling, as a fallback.

### C7. The payment stop is only a prompt
The agent reads untrusted supplier pages, which can contain prompt-injection text. **[Verified]** jev-1.13 weak point: adversarial state can shift answers. An LLM instruction is not a guardrail.

**Fix:** use a **code-enforced stop** with three layers:
1. After each step, the agent calls a `page_state` tool (Jev, J6). If P(payment step) ≥ 0.2, code ends the run. The threshold is low on purpose, because a false stop costs little and a false continue costs a lot.
2. A DOM check runs too: if the page has inputs with `autocomplete="cc-number"` or similar names, code ends the run.
3. The browser never receives any card data. The simulated card stays in the backend.

### C8. The spending rules are checked too late
In the plan, the rules run after approval, so the owner can approve a plan that the rules then block.

**Fix:** check the rules in stage 9. Show a "within your rules" badge on each plan before approval. Mint the cards at approval.

---

## 2. Medium issues

| # | Issue | Fix |
|---|---|---|
| M1 | The optimizer limit is wrong. "Keep 3 options for each item if there are more than 500 combinations" still gives 3⁶ = 729 for 6 items. | For each item and supplier, keep only the cheapest matched option. Then list every way to give items to suppliers: suppliers^items ≤ 4⁸ ≈ 65k. Python does this in milliseconds. Free-shipping thresholds make a greedy method wrong, so list all of them. |
| M2 | The SMS channel has no clear job. The auto-replier makes it look fake. | Make SMS part of the **distributor conversation**: 1. SMS request. 2. If there is no reply in N seconds, call. 3. Send an SMS summary of the call. This gives Photon's "remember the call when the conversation moves back to text". Keep the auto-replier for one phone-only supplier, or cut it. |
| M3 | CREAO's job is weak if the 3 sites are fixed at H0. | CREAO returns 8–10 candidates with contact details. Jev (J3) filters them. Known sites use fast adapters, and unknown sites use the LLM agent. CREAO also returns the distributor's phone and email. Without CREAO, no supplier list exists. |
| M4 | Two models write the warnings. | Make each warning a code template filled from Jev answers, for example "Staples gloves: size medium not confirmed (p=0.31)". Step 5 Preview writes only the one-paragraph summary. These warnings can be checked and labelled. |
| M5 | It is not clear which bill lines are supplies. | Jev J1 sorts the bill lines into groups. Insights then work for any company, and this is a strong Intuit story. |
| M6 | Guest checkout needs a real address that passes validation. | Use a team-controlled or university address. Never use a third party's address. Some sites ask for a phone number: use the Photon number. |
| M7 | The evidence files have no home. | The backend saves screenshots and DOM extracts under `/evidence/{jobId}/` and serves them as static files. The events carry the URLs. |
| M8 | Choice option order changes Jev's answers ([Verified], jev-1.13 weak point). | In the Intuit evaluation, shuffle the option order and measure how often the answer stays the same. Put the most common option last, not first. |

---

## 3. Where Jev fits, and where it does not

**Rule:** use Jev for a decision that has a closed set of answers, happens many times, needs to be fast, and has a threshold whose cost we can state. Keep generation, multi-step navigation and arithmetic away from Jev.

### 3.1 Jev decision points

| ID | Decision | State (keep it small) | Questions | Code uses it for | Calls per run |
|---|---|---|---|---|---|
| **J1** | Bill-line triage | One bill line (item, description, vendor) | Choice `category` {food ingredient, packaging & disposables, cleaning & safety, smallwares, equipment, service, other}. Noul "a commodity item that online supply retailers sell" | Insights screen, the prefilled list | ~one per distinct item (cache) |
| **J3** | Supplier filter | A CREAO supplier record | Nouls: sells this category · shows prices without a login · is a supplier (not a marketplace, blog or directory) · ships to businesses | Which candidates get a browser lane | ~10 |
| **J4** | Search-result rerank | Spec + the list of result titles and snippets from the DOM | One **Score** for each result: {different product · related, not the same · same product} | Open the top 1–3 results only | ~18 requests (item × site), ~10 questions each |
| **J5** | Listing judge | Spec + product page extract (title, bullets, spec table, regex candidates) | **Score** match (3 levels, as in the entity-alignment cookbook). **Noul** for each `mustHave`. **Choice** `packCount` among the regex candidates + "not stated". **Noul** in stock | `matchScore`, flags, `unitsPerPack`. Code computes cost per unit | ~54 (one per opened listing) |
| **J6** | Page-state guard | URL + visible text (cut) + form-field labels | **Choice** {search results, product page, cart, shipping/address step, **payment step**, login wall, bot check, out of stock/error, other} | Hard stop at payment (≥0.2). Login wall or bot check → fallback | ~5–15 for each session |
| **J7** | Cart totals reader | The cart page's money values (found by regex), each with its nearby label text | Four Choices: which candidate is the subtotal / shipping / tax / total ("none" allowed) | Code checks subtotal + shipping + tax + fees = total. **A mismatch means not verified** | ~1 for each cart state |
| **J8** | Reply intent + price selection | One SMS or email reply + the regex money candidates | Choice intent {quote, question, decline, will reply later, unrelated}. One Choice for each item: which candidate is its price | Follow-up logic, the quote | small |
| **J9** | Live call line | Each finished transcript line from the supplier + the recent context | Noul "the supplier states a new price or offer". Choice for which item or the total. Noul "the supplier agrees to beat the price" | Instant `rerank` event: **this drives the live wow moment** | ~20 |
| **J10** | Quote risk terms | The quote text or transcript | Nouls: price depends on volume or time · delivery fee mentioned · availability uncertain or backorder | Warnings, reliability score | small |

**Cost:** about 150 requests × about 2k input tokens ≈ 300k tokens ≈ **$0.013 per run** (**[Verified]** price: $0.042 for each million input tokens, and output is free).
**Latency:** TypeSafe publishes no latency numbers. The brief's 70–500 ms is a company claim **[Unverified]**. Measure p50 and p95 at H4.
**Concurrency:** the cookbook used 6 workers because of rate limits above about 8. Put a semaphore of 6 in the Jev client.

### 3.2 `decide()` becomes a cascade that knows the stakes
```
decide(question_set, state, stakes) ->
  1. Jev answer (+ probabilities / confidence)
  2. if stakes == "safety" (J6): never escalate; when unsure, take the safe branch
  3. elif confidence in act band: return the answer
  4. elif confidence in confirm band: escalate to Step 5 Preview with the same state + question
  5. log Jev answer, Step 5 answer, latency and tokens  -> this becomes the Intuit evaluation data
```
Set the thresholds for each decision. Start them strict, then tune them on the cached search results. Never use one global threshold.

### 3.3 Where not to use Jev
| Task | Use instead | Reason |
|---|---|---|
| Landed cost, plans, totals, savings, cadence | Code | [Verified] Jev weak points: math and dates |
| Search query text, SMS and email drafts, the results summary | Step 5 Preview | [Verified] Jev does not generate text |
| Moving through an unknown site | Browser Use agent (Step 5 Preview or the fallback model) | Many steps, and the actions are open-ended |
| Reading prices from screenshots | DOM text + J7 | [Verified] Jev image input is "not yet … ready for production". Reading text from images is not documented |
| The live phone conversation | ElevenLabs agent | Real-time voice turns |

---

## 4. Revised pipeline

```
Screen 0 opens
 ├─ load books → J1 triage → insights                     (code + Jev)
 └─ CREAO discovery (cached by category+ZIP) → J3 filter   (CREAO + Jev)

"Compare suppliers"
 ├─ Step 5 Preview: specs for each item (parallel)
 ├─ Supplier lane ×3 (parallel, ONE session each):
 │    search URL → DOM results → J4 → open top-k → J5 → add matched items
 │    → cart → J7 totals (verified "everything from X")  ──► event: supplier_cart_ready
 │    (J6 after every navigation; code-enforced stop)
 ├─ Distributor lane: SMS request (Step 5 draft) → J8 → no reply in N s → CALL
 │    call opens with the best plan so far → J9 on each line → rerank event
 └─ Optimizer runs again on each supplier_cart_ready / quote event       (code)
       → for the top plans, lanes remove items and re-read subset totals (J7)

Results: code ranks · rules checked BEFORE approval · Step 5 writes the summary · warnings from J5/J10
Approve → mint simulated cards → each lane sets the cart to the plan subset → checkout
       → J6 sees shipping step → fill team address → J6 sees payment step → STOP → live view
```

### 4.1 Time budget (demo target)
| Segment | Fast path (scripted + Jev) | LLM-agent path |
|---|---|---|
| Specs (Step 5, parallel) | 3–6 s | same |
| Search + judge for each site (parallel) | 15–30 s | 60–180 s |
| Full cart + totals | 15–30 s | 45–120 s |
| Subset totals, top 2 plans | 10–20 s | 30–60 s |
| SMS wait → call | 10 s + 60–90 s call | same |
| **Live total** | **≈ 2–2.5 min** | 5–8 min |

All numbers are estimates **[Unverified]**. Time them at H10. Only the fast path fits the 3-minute script. Use the LLM-agent path for one "unknown site" tile, to show agentic browsing without blocking the plan.

---

## 5. Architecture changes

1. **Browser worker:** the open-source `browser_use.Agent` with custom tools in all environments.
   - Browser: local in development, `Browser(use_cloud=True)` in the demo.
   - Model: `ChatOpenAI(base_url=OpenRouter, model=step-5-preview)` (H3 gate). Fallback: a proven model.
   - **Custom tools** (`@tools.action`, [Verified] supported): `rank_results` (J4), `judge_listing` (J5), `page_state` (J6), `read_cart_totals` (J7). Each one calls the backend's Jev client.
   - Known sites also get scripted Playwright adapters that call the same Jev functions.
2. **`shared/jev.py`:**
   - Client with a semaphore of 6, retries on 429 and 529, and a **cache keyed on (state, questions)**. The cookbook recommends caching every call. The cache also makes the replay switch exact.
   - Sentry spans for each call, with tokens, latency and confidence.
3. **`decide()`:** the stakes-aware cascade in §3.2. It logs every pair of Jev and Step 5 answers to SQLite as evaluation data.
4. **Optimizer:** runs incrementally on events and lists every assignment (M1).
5. **Tunnel** at H0 for the Photon and ElevenLabs webhooks (C6).
6. **Evidence store:** files under `/evidence/{jobId}/`, served as static files (M7).
7. **Event log = the replay source:** record each event with its time offset. The replay switch plays the log at real speed.

---

## 6. Contract changes

| Contract | Change |
|---|---|
| `SupplierOffers.products[]` | Remove the agent-reported `matchConfidence`. Add `rawListing` (the extract), `packCandidates[]`, `jev: { matchScore, matchProbs, mustHave: {attr: p}, inStock: p }`. Code computes `unitsPerPack` from the chosen candidate. |
| `SupplierOffers` | Add `source`, plus `bundleTotal` for a quote that is one total for the full list (C5). |
| `VerifiedCart` | `sessionId` is required. Add `totalsCheck: "consistent" \| "mismatch"` from J7 + code. A mismatch means not verified. |
| `CandidatePlans` | Add `rulesCheck` (C8). Remove the limit of 500 combinations. |
| Events | Add `supplier_cart_ready`, `subset_total`, `page_state {lane, state, p}`, `guard_stop {lane, reason}`, `call_offer {price, p}`. |

---

## 7. Intuit evaluation, simpler now

The labelled data comes from normal runs:
- **J5 listing matches:** label 150–200 (spec, listing) pairs from the cached searches.
- **J8 reply intents:** from the auto-replier script, plus hand-written variants.
- **J6 page states:** a few dozen page captures.

Compare three methods: Jev, Step 5 Preview (asked the same question, asked to give JSON), and string similarity (for J5). Report:
- accuracy
- calibration (Brier score and a reliability plot)
- p50 and p95 latency
- cost for each 1,000 decisions
- answer stability when the option order is shuffled

The `decide()` log already holds the paired answers.

---

## 8. Open items added by this review

1. Does the open-source agent + `Browser(use_cloud=True)` give a live view URL? (H0)
2. Do we have a paid Browser Use key? The free plan's 15-minute session limit is too short for the screen 4 handoff.
3. Can Photon and ElevenLabs send events through a tunnel, or can we poll them?
4. Jev access: confirm the key, the rate limit (tokens per second and requests per second in the console) and the measured latency at H4.
5. A team-controlled checkout address and email.
6. Thresholds for J4, J5 and J6. Set them on the first cached runs and record them in the spec.

## 9. Recommended next steps

1. Fold C1–C8 and §6 into the alignment spec and `DATA_FLOW.md`. Then update the system map.
2. Scaffold the repo with `shared/jev.py`, `decide()`, the contracts and fixtures first. The J5 and J7 functions can be built and tested on saved HTML before any live browser work.
