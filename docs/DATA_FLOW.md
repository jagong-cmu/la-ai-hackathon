# Data Contracts (v2)

> **v2, Oct 16, 2026.** This file is the one source of truth for the data shapes, the API and the events.
> The decisions, the pipeline and the reliability rules are in `.plans/supplier-agent-alignment-spec.md`. The reasons for each change from v1 are in `.plans/architecture-review.md`.
>
> Conventions:
> - camelCase field names.
> - Money is a number in USD with 2 decimal places. Code rounds once, at display time.
> - Times are ISO 8601 UTC.
> - Write every contract once as a Pydantic v2 model in `shared/contracts.py`, and generate the TypeScript types for the frontend from it.
> - The fields that **Jev** fills are inside a `jev` object. The fields that **code** computes are never filled by a model.

---

## 1. Request side

### 1.1 `SpendInsights` (screen 0)
```json
{
  "businessId": "company-001",
  "periodDays": 365,
  "supplySpend": 18430.00,
  "supplyOrders": 26,
  "items": [
    {
      "itemId": "gloves",
      "billItemName": "Nitrile Gloves, M, 100 ct",
      "category": "packaging_disposables",
      "jev": { "categoryProbs": { "packaging_disposables": 0.93, "cleaning_safety": 0.06, "other": 0.01 }, "onlineBuyable": 0.91 },
      "pastUnitPrice": 0.14,
      "pastOrderQty": 2000,
      "orderCadenceDays": 14,
      "yearSpend": 7280.00,
      "sourceBillIds": ["bill-syn-0003", "bill-syn-0007"],
      "synthetic": true
    }
  ]
}
```

### 1.2 `ShoppingRequest` (screen 1 → `POST /jobs`)
```json
{
  "businessId": "company-001",
  "items": [
    {
      "id": "gloves", "name": "Medium nitrile gloves", "quantity": 2000, "unit": "glove",
      "pastUnitPrice": 0.14, "orderCadenceDays": 14, "sourceBillIds": ["bill-syn-0003"],
      "spec": { "$ref": "SearchSpec" }
    }
  ],
  "suppliers": [ { "id": "uline", "url": "https://www.uline.com", "adapter": "uline", "origin": "creao" } ],
  "distributor": { "id": "dist", "name": "(fictional distributor)", "phone": "+1…", "email": "…" },
  "zipCode": "49007",
  "acceptQuotedDefault": false
}
```
- `suppliers[].adapter` is `null` for an unknown site. That lane uses the LLM agent.
- The request header `Idempotency-Key` is required.

### 1.3 `SearchSpec` (Step 5 Preview)
```json
{
  "itemId": "gloves",
  "query": "medium nitrile gloves powder free",
  "attributes": { "size": "M", "material": "nitrile", "powderFree": true },
  "mustHave": ["nitrile", "size medium"],
  "niceToHave": ["food safe"],
  "unit": "glove",
  "acceptablePackSizes": [100, 200, 1000]
}
```
Fallback when Step 5 fails: `query = billItemName`, `mustHave = []`, with the flag `specFallback: true`.

### 1.4 `SupplierCandidate` (CREAO + J3)
```json
{
  "id": "uline", "name": "Uline", "url": "https://www.uline.com", "phone": "…", "email": null,
  "jev": { "sellsCategory": 0.97, "pricesWithoutLogin": 0.88, "isSupplier": 0.96, "shipsToBusiness": 0.95 },
  "selected": true, "reason": "known adapter",
  "creaoRunId": "…", "cachedAt": "2026-10-17T09:12:00Z"
}
```

---

## 2. Supplier lane

### 2.1 `LaneStatus`
```json
{
  "laneId": "lane_uline", "supplierId": "uline", "mode": "adapter | llm_agent",
  "state": "queued | searching | carting | cart_ready | subset_pricing | idle | checkout | at_payment | failed | blocked",
  "reason": null,
  "sessionId": "bu_…", "liveUrl": "https://…", "lastScreenshotUrl": "/evidence/job_01/uline/0007.png",
  "budgets": { "searchS": 45, "cartS": 45, "subsetS": 20 },
  "startedAt": "…", "updatedAt": "…"
}
```
`reason` values: `timeout`, `guard_stop`, `guard_unavailable`, `login_wall`, `bot_check`, `out_of_stock`, `adapter_error`, `session_expired`.

### 2.2 `SupplierOffers` (lane search output)
```json
{
  "supplierId": "uline",
  "source": "web",
  "status": "ok | partial | not_found | blocked | failed",
  "products": [
    {
      "itemId": "gloves",
      "title": "Uline Industrial Nitrile Gloves - Medium (Box of 100)",
      "sku": "S-12345M",
      "url": "https://www.uline.com/…",
      "rawListing": "/evidence/job_01/uline/gloves_listing.json",
      "packCandidates": [ { "text": "Box of 100", "value": 100 }, { "text": "10 boxes/case", "value": 10 } ],
      "packPrice": 13.00,
      "jev": {
        "matchScore": 1.92,
        "matchProbs": { "0": 0.01, "1": 0.06, "2": 0.93 },
        "mustHave": { "nitrile": 0.99, "size medium": 0.97 },
        "packCount": { "choice": "Box of 100", "confidence": 0.94 },
        "inStock": 0.95,
        "escalated": false
      },
      "unitsPerPack": 100
    }
  ],
  "minimumOrderValue": 0,
  "freeShippingThreshold": null,
  "evidence": { "screenshotUrls": [], "visitedUrls": [] },
  "error": null
}
```
**Rules:**
- `packPrice` comes from a regex on the price element, and code parses it. Jev chooses only between candidates, and only when there is more than one.
- `unitsPerPack` = the `value` of the chosen `packCandidates` entry, in the request's unit. If Jev chooses "not stated", the product gets the flag `pack_unknown` and is left out of the plans.
- The match levels are 0 = different product, 1 = related but not the same, 2 = same product (as in the entity-alignment cookbook).

### 2.3 Quoted offers (distributor)
```json
{
  "supplierId": "dist",
  "source": "sms | email | call",
  "status": "ok",
  "products": [ { "itemId": "gloves", "packPrice": 120.00, "unitsPerPack": 1000, "jev": { "priceChoice": { "choice": "$120 a case", "confidence": 0.88 } } } ],
  "bundleTotal": null,
  "quotedDeliveryFee": 0.00,
  "quotedLeadTimeDays": 2,
  "jevTerms": { "conditionalPrice": 0.12, "deliveryFeeMentioned": 0.91, "availabilityRisk": 0.08 },
  "evidence": { "transcriptUrl": "/evidence/job_01/dist/call.txt", "messageIds": ["sms_3"] }
}
```
- If the distributor gives only a total for the full list, `products` is empty and `bundleTotal` is set. The quote then becomes one plan of kind `quoted_bundle` (§3.2).

---

## 3. Optimization

### 3.1 `NormalizedOffers`
For each (item, supplier), keep the cheapest option that is **eligible**:
- `available`,
- `matchProbs["2"] ≥ act threshold`,
- all `mustHave` values ≥ their threshold,
- and the pack count is known.

```json
{
  "itemId": "gloves", "supplierId": "uline", "sku": "S-12345M",
  "requestedQuantity": 2000, "unitsPerPack": 100, "packPrice": 13.00,
  "packsNeeded": 20, "unitsBought": 2000, "overbuyUnits": 0,
  "lineCost": 260.00, "effectiveUnitPrice": 0.13,
  "flags": [], "eligible": true
}
```
Code formulas:
- `packsNeeded = ceil(requestedQuantity / unitsPerPack)`
- `lineCost = packsNeeded × packPrice`
- `effectiveUnitPrice = lineCost / requestedQuantity`

Flags: `low_match` (match in the confirm band), `overbuy` (> 25%), `pack_unknown`, `must_have_unsure:<attr>`.

### 3.2 `CandidatePlans`
- **Enumeration:** every assignment of each item to one eligible supplier (≤ 4⁸ combinations). Plus each single-supplier plan, plus `quoted_bundle` if it exists.
- **Shipping:** use the **verified** cart values when that supplier has a verified cart. Otherwise use the free-shipping threshold rule, or $8.00 with `shippingIsEstimate: true`.
- Remove a plan when a cart is under its supplier's minimum order value.

```json
{
  "planId": "plan_3",
  "kind": "split | single_supplier | quoted_bundle",
  "assignments": [ { "itemId": "gloves", "supplierId": "uline", "sku": "S-12345M", "packQuantity": 20 } ],
  "carts": ["uline:S-12345Mx20", "webstaurant:B-88x4"],
  "estimatedTotal": 412.40,
  "verifiedTotal": null,
  "status": "estimated | verified | quoted",
  "rulesCheck": { "pass": true, "failed": [] },
  "rank": 1
}
```
- `cartKey` = `supplierId:` + the sorted `sku×qty` list. Two plans with the same cart share one subset total.
- Subset totals are priced only for the **top 2 split plans**, and only before the deadline.

---

## 4. Verified carts

### 4.1 `VerifiedCart`
```json
{
  "cartKey": "uline:S-12345Mx20",
  "supplierId": "uline",
  "sessionId": "bu_…",
  "cartItems": [ { "itemId": "gloves", "sku": "S-12345M", "packQuantity": 20, "unitPrice": 13.00, "lineTotal": 260.00 } ],
  "moneyCandidates": [ { "label": "Subtotal", "value": 260.00 }, { "label": "Est. Shipping", "value": 18.40 }, { "label": "Total", "value": 278.40 } ],
  "costs": { "subtotal": 260.00, "shipping": 18.40, "tax": 0.00, "otherFees": 0.00, "total": 278.40 },
  "jev": { "subtotal": 0.97, "shipping": 0.93, "tax": 0.88, "total": 0.98 },
  "totalsCheck": "consistent | mismatch",
  "minimumOrderMet": true,
  "verificationStatus": "verified | price_changed | out_of_stock | blocked | failed",
  "priceChanges": [],
  "screenshotUrl": "/evidence/job_01/uline/cart_1.png",
  "verifiedAt": "…"
}
```
- **Rule:** `verified` requires `totalsCheck = consistent`: |subtotal + shipping + tax + otherFees − total| ≤ $0.01.
- When the check fails, the status is `failed`, with the reason `totals_mismatch`.
- A `tax` of "none" from Jev means $0.00 with the flag `tax_not_shown`.

---

## 5. Results and approval

### 5.1 `JobResults`
```json
{
  "jobId": "job_01",
  "status": "complete | partial",
  "deadlineHit": false,
  "recommendedPlanId": "plan_3",
  "plans": [ { "$ref": "CandidatePlans item + carts summary" } ],
  "quotedOffer": { "planId": "plan_q", "total": 395.00, "status": "quoted", "transcriptUrl": "…" },
  "savingsVsPaidBefore": 61.20,
  "projectedYearlySavings": 1591.20,
  "warnings": [ { "code": "must_have_unsure", "itemId": "gloves", "supplierId": "staples", "text": "Staples gloves: size medium not confirmed (p=0.31)" } ],
  "summary": "Step 5 Preview paragraph, or a template when Step 5 is unavailable",
  "laneOutcomes": [ { "supplierId": "staples", "state": "blocked", "reason": "bot_check" } ]
}
```
- **Recommendation rule:** the cheapest plan with `status = verified`, or the cheapest `quoted` plan if the owner accepted quoted offers, **and** `rulesCheck.pass`.
- When no plan qualifies, `recommendedPlanId` is null, and the UI says why.

### 5.2 `ApprovalDecision` (`POST /jobs/{id}/approve`, can be repeated safely)
`{ "action": "approve" | "reject", "planId": "plan_3", "acceptQuoted": false, "decidedBy": "owner", "reason": null }`

### 5.3 `VirtualCard` (simulated)
`{ "cardId": "vc_1", "supplierId": "uline", "last4": "4821", "limitUsd": 283.97, "merchantLock": "uline.com", "expiresAt": "…", "status": "ready", "simulated": true }`
- The card stays in the backend. No card data goes to a browser session or to an agent prompt.

### 5.4 `CheckoutHandoff`
`{ "supplierId": "uline", "status": "at_payment | failed | quoted_confirm_by_phone", "reason": null, "liveUrl": "…", "screenshotUrl": "…", "cardId": "vc_1" }`

---

## 6. Jev decision log (`decisions` table, the Intuit evaluation data)
`{ "decisionId", "jobId", "jId": "J5", "stateHash", "questions", "jevAnswer", "jevLatencyMs", "jevTokens", "band": "act | confirm | unsure", "escalated": true, "step5Answer", "step5LatencyMs", "final", "label": null }`
- `label` is filled during the evaluation (H18+).

---

## 7. API

| Method | Path | Notes |
|---|---|---|
| GET | `/insights?businessId=` | `SpendInsights` |
| GET | `/suppliers?category=&zip=` | Discovery result + cache age |
| POST | `/jobs` | `ShoppingRequest` → `{ jobId }`. Needs `Idempotency-Key`. |
| GET | `/jobs/{id}` | The full current state: job, lanes, offers, plans, results |
| GET | `/jobs/{id}/events` | SSE. Supports `Last-Event-ID`. |
| POST | `/jobs/{id}/approve` | `ApprovalDecision`. Can be repeated safely. |
| GET | `/evidence/{jobId}/…` | Static evidence files |
| GET | `/health/preflight` | The preflight checks (spec §10.7) |
| POST | `/admin/mode` | Set `live` or `cached` for each dependency. Admin token required. (Replay is a separate route: `POST /admin/replay/{jobId}`.) |
| POST | `/webhooks/photon`, `/webhooks/elevenlabs` | Through the tunnel. Shared secret + dedupe by event id. |

---

## 8. Events (SSE)

Every event: `{ "seq": 42, "jobId": "job_01", "type": "…", "ts": "…", "laneId": null, "data": {} }`. `seq` increases without gaps for each job.

| Group | Types |
|---|---|
| Job | `job_started` · `deadline_hit` · `results_ready` · `results_updated` · `approved` · `rejected` · `job_failed` |
| Discovery | `discovery_started` · `discovery_done {count, cached, ageS}` |
| Specs | `spec_ready {itemId, fallback}` |
| Lane | `lane_state {state, reason}` · `page_state {state, p}` · `guard_stop {reason}` · `offers_ready` · `supplier_cart_ready {cartKey, total, totalsCheck}` · `subset_total {cartKey, total}` · `screenshot {url}` |
| Optimizer | `plans_updated {bestPlanId, bestTotal, status}` |
| Distributor | `message_sent` · `reply_received {intent}` · `call_started` · `transcript_line {speaker, text}` · `call_offer {itemId?, price, p}` · `call_ended` · `quote_received {source}` |
| Approval | `rules_checked` · `card_minted {supplierId, last4}` · `handoff_ready {supplierId, liveUrl}` |
| System | `dependency_degraded {dep, mode}` · `replay_started` · `job_state {state}` |
| Lane (live) | `lane_fallback {from, to, step, error}`: an adapter step failed and the LLM agent took over on the same browser · `checkout_filled {fields, zip}` |
| Distributor (extra) | `call_skipped {reason}` |

---

## 9. Restock planner (spec §13)

All shapes are in `backend/app/planner/schemas.py`. JSON is camelCase, as everywhere else.

| Method | Path | Returns |
|---|---|---|
| GET | `/planner` | `PlannerView`: `products[]` (history with forecast ranges and misses; 6-week forecast), `supplies[]` (`SupplyPlan`), `misses[]` (`MissView`), `adjustments[]`, `disclosure` |
| POST | `/planner/check?autostart=true` | `PlannerCheck`: the supplies due now. With `autostart`, it also starts a sourcing job with the planner's quantities. A CREAO scheduled run calls this route once a day. |
| POST | `/planner/owner-messages` | `{text, receivedAt?}` → `ForecastAdjustment` (J12). `status`: `applied`, `needs_confirmation` or `ignored`. |
| POST | `/planner/adjustments/{id}/confirm?accept=true` | Confirms or rejects an adjustment in `needs_confirmation`. |
| POST | `/planner/misses/{missId}/answer` | `{cause}` → the owner's answer to a J11 question. It is applied and saved as a label. |
| GET | `/planner/backtest` | `BacktestReport`: variants `plain`, `fixed_rule` and `jev`, with WAPE, bias, coverage, misses, owner questions and the stock simulation. `jevSource` is `stub` in fixture mode. |

`SupplyPlan` example (real output of a fixture run):
```json
{ "itemId": "syn-bread-bags", "unit": "bag", "pack": 1000, "onHand": 4288.0,
  "weeklyUseForecast": [{ "weekStart": "2026-10-05", "mean": 2496.6, "low": 255.3, "high": 4737.9 }],
  "safetyStock": 3179.3, "orderUpTo": 8213.9, "recommendedQty": 4000,
  "runOutDate": "2026-10-16", "orderByDate": "2026-10-05", "status": "order_now",
  "drivers": { "item-00003": 12.0, "item-00006": 6.0 }, "ratiosAssumed": true }
```

`MissView` example (real output, facts cut to 2): `{ "missId": "miss_item-00001_15", "bucket": "far below the range", "facts": ["Customer Lowry & Daughters Stationers stopped ordering: no order this week or last week (usually about 82 a week).", "Supplier bill note 0 weeks ago: \"Partial backorder noted; balance to follow separately.\""], "cause": "supply_shortage", "band": "act", "status": "applied", "source": "stub" }`

## 10. Changes from v1 (summary)
- One session for each supplier. `sessionId` is required. The checkout handoff replaces Execute.
- The agent no longer reports `matchConfidence`. Jev answers live in `jev` objects, and code computes `unitsPerPack` from regex candidates.
- `totalsCheck` is a code sum check. Without it, a cart is not verified.
- The 500-combination limit is removed. Every assignment is listed.
- `bundleTotal` and the `quoted_bundle` plan kind are new.
- `rulesCheck` runs before approval.
- New events for lanes, the guard, live call offers and degraded dependencies.
- The demo ZIP code is Kalamazoo (49007), not 90024.
