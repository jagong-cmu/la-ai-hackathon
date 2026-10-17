"""The Jev decision points J1-J10 (spec §6): question design + interpretation.

Pattern: code finds candidates (regex over DOM text), Jev selects, code computes.
Keep `state` to the filtered fields each question needs.
Choice option order: Jev leans toward the first option, so put the safe or rare option
first where a bias toward it is acceptable (J6 lists payment_step first on purpose).
"""

from __future__ import annotations

from typing import Any

from app.contracts import (
    JevListing,
    MoneyCandidate,
    PackCandidate,
    PageSnapshot,
    SearchSpec,
    SupplierCandidate,
)
from app.decide import Decider, Decision, Stakes
from app.jev import choice, choice_confidence, noul, score
from app.stages.extract import pack_candidates

MATCH_LEVELS = [
    "Different product: a different item type, material or size class than the spec.",
    "Related, but possibly not the same: same item type, but a must-have attribute differs or is not shown.",
    "Same product: the listing matches the spec's item type and every must-have attribute.",
]

CATEGORIES = {
    "food_ingredient": "Food or ingredients the business turns into products.",
    "packaging_disposables": "Boxes, bags, liners, gloves, wraps, cups and other disposables.",
    "cleaning_safety": "Cleaning chemicals, towels, sanitation and safety supplies.",
    "smallwares": "Reusable kitchen tools, pans, utensils.",
    "equipment": "Machines and large equipment.",
    "service": "Services, rent, utilities, subscriptions.",
    "other": "None of the above.",
}

PAGE_STATES = {
    # payment_step first: a first-option bias toward the safe answer is acceptable here.
    "payment_step": "The page asks for payment details or to place or pay for the order.",
    "shipping_step": "Checkout page that asks for a shipping or delivery address.",
    "cart": "Shopping cart with items and totals, before checkout.",
    "product_page": "One product's page with price and an add-to-cart control.",
    "search_results": "A list of products that match a search.",
    "login_wall": "The site requires sign-in before showing prices or continuing.",
    "bot_check": "A CAPTCHA or a human-verification challenge.",
    "out_of_stock": "An error page, or the product is unavailable or out of stock.",
    "other": "Anything else.",
}


# ---------------------------------------------------------------- J1 bill-line triage


async def j1_triage(d: Decider, item_name: str, description: str, vendor: str) -> Decision:
    state = {"itemName": item_name, "description": description, "vendor": vendor}
    questions = {
        "category": choice(
            "Which spending category does the purchased item in `itemName` belong to?", CATEGORIES
        ),
        "online_buyable": noul(
            "Is the item in `itemName` a standard commodity that online supply retailers sell to businesses?",
            true="A common supply item with listed prices online.",
            false="Custom, perishable, local-only, or a service.",
        ),
    }
    return await d.decide("J1", state, questions, primary=["category"])


# ---------------------------------------------------------------- J3 supplier filter


async def j3_supplier(d: Decider, cand: SupplierCandidate, category: str, notes: str) -> Decision:
    state = {"name": cand.name, "url": cand.url, "notes": notes, "category": category}
    questions = {
        "sells_category": noul("Does this supplier sell items in `category`?"),
        "prices_without_login": noul("Does this supplier show prices to visitors without signing in?"),
        "is_supplier": noul(
            "Is this a business that sells and ships products itself?",
            false="A marketplace listing, a blog, a review site or a directory.",
        ),
        "ships_to_business": noul("Does this supplier ship orders to businesses in the US?"),
    }
    return await d.decide("J3", state, questions, job_id=None)


# ---------------------------------------------------------------- J4 search-result rerank


async def j4_rerank(
    d: Decider, spec: SearchSpec, results: list[dict[str, str]], job_id: str | None = None
) -> list[tuple[int, float]]:
    """results: [{"title": ..., "snippet": ...}] from the DOM. Returns (index, P(same)) best first."""
    state = {"spec": spec.model_dump(by_alias=True), "results": results}
    questions = {
        f"r{i}": score(
            f"How well does `results[{i}]` match the product described in `spec`?", MATCH_LEVELS
        )
        for i in range(len(results))
    }
    decision = await d.decide("J4", state, questions, job_id=job_id, act=0.0)
    ranked = [
        (i, float(decision.answers[f"r{i}"].get("probabilities", {}).get("2", 0.0)))
        for i in range(len(results))
    ]
    return sorted(ranked, key=lambda t: t[1], reverse=True)


# ---------------------------------------------------------------- J5 listing judge


async def j5_listing(
    d: Decider, spec: SearchSpec, title: str, listing_text: str, job_id: str | None = None
) -> tuple[JevListing, list[PackCandidate], int | None]:
    """Returns the Jev listing judgment, the regex pack candidates and the chosen units per pack."""
    candidates = pack_candidates(f"{title}\n{listing_text}")
    state = {
        "spec": spec.model_dump(by_alias=True),
        "listing": {"title": title, "text": listing_text[:3000]},
        "packCandidates": [c.text for c in candidates],
    }
    questions: dict[str, Any] = {
        "match": score("How well does `listing` match the product described in `spec`?", MATCH_LEVELS),
        "in_stock": noul("Does `listing` show that the product is in stock and can be ordered now?"),
    }
    for i, attr in enumerate(spec.must_have):
        questions[f"must_{i}"] = noul(f"Does `listing` clearly meet this requirement: {attr}?")
    if len(candidates) > 1:
        options = {c.text: None for c in candidates}
        options["not stated"] = "The listing does not say how many units one purchase contains."
        questions["pack"] = choice(
            f"Which phrase in `packCandidates` states how many {spec.unit} units one purchase of this listing contains?",
            options,
        )
    decision = await d.decide("J5", state, questions, primary=["match"], job_id=job_id)
    a = decision.answers
    probs = {k: round(float(v), 4) for k, v in a["match"].get("probabilities", {}).items()}
    units: int | None = None
    pack_info: dict[str, Any] | None = None
    if len(candidates) == 1:
        units = candidates[0].value
        pack_info = {"choice": candidates[0].text, "confidence": 1.0, "source": "single_candidate"}
    elif "pack" in a:
        pick = a["pack"]["choice"]
        conf = a["pack"].get("confidence")
        if conf is None and a["pack"].get("probabilities"):
            conf = choice_confidence(a["pack"]["probabilities"])
        pack_info = {"choice": pick, "confidence": conf}
        units = next((c.value for c in candidates if c.text == pick), None)
    listing = JevListing(
        match_score=round(float(a["match"].get("score", 0.0)), 4),
        match_probs=probs,
        must_have={attr: float(a[f"must_{i}"]["noul"]) for i, attr in enumerate(spec.must_have)},
        pack_count=pack_info,
        in_stock=float(a["in_stock"]["noul"]),
        escalated=decision.escalated,
    )
    return listing, candidates, units


# ---------------------------------------------------------------- J6 page state (safety)


async def j6_page_state(d: Decider, page: PageSnapshot, job_id: str | None = None) -> Decision:
    state = {
        "url": page.url,
        "visibleText": page.visible_text[:4000],
        "formFields": [i.label or i.name or i.id for i in page.inputs if (i.label or i.name or i.id)],
    }
    questions = {"page": choice("What kind of page is the browser on?", PAGE_STATES)}
    return await d.decide("J6", state, questions, stakes=Stakes.safety, job_id=job_id)


# ---------------------------------------------------------------- J7 cart totals


TOTAL_FIELDS = {
    "subtotal": "the merchandise subtotal, before shipping and tax",
    "shipping": "the shipping or delivery charge",
    "tax": "the sales tax",
    "total": "the final order total that the buyer would pay",
}


async def j7_totals(
    d: Decider, candidates: list[MoneyCandidate], job_id: str | None = None
) -> tuple[dict[str, int | None], dict[str, float]]:
    """Returns the chosen candidate index per field (None = not shown) and the confidences."""
    state = {
        "candidates": [
            {"id": f"c{i}", "label": c.label, "amount": f"${c.value:.2f}"}
            for i, c in enumerate(candidates)
        ]
    }
    options = {f"c{i}": f"{c.label}: ${c.value:.2f}" for i, c in enumerate(candidates)}
    options["none"] = "None of the amounts is this value."
    questions = {
        name: choice(f"Which amount in `candidates` is {desc}?", options)
        for name, desc in TOTAL_FIELDS.items()
    }
    decision = await d.decide("J7", state, questions, job_id=job_id)
    picks: dict[str, int | None] = {}
    confs: dict[str, float] = {}
    for name in TOTAL_FIELDS:
        ans = decision.answers.get(name, {})
        pick = ans.get("choice", "none")
        picks[name] = None if pick == "none" else int(pick[1:])
        confs[name] = float(ans.get("confidence") or 0.0)
    return picks, confs


# ---------------------------------------------------------------- J8 reply intent + price


REPLY_INTENTS = {
    "unrelated": "The message is not about this quote request.",
    "will_reply_later": "The supplier says they will answer later.",
    "decline": "The supplier declines to quote.",
    "question": "The supplier asks a question before quoting.",
    "quote": "The supplier gives one or more prices.",
}


async def j8_reply(
    d: Decider, message: str, item_names: dict[str, str], money: list[MoneyCandidate],
    job_id: str | None = None,
) -> Decision:
    state = {
        "message": message,
        "items": item_names,
        "amounts": [f"c{i}: {m.label} ${m.value:.2f}" for i, m in enumerate(money)],
    }
    questions: dict[str, Any] = {
        "intent": choice("What does the supplier's `message` do?", REPLY_INTENTS)
    }
    options = {f"c{i}": None for i in range(len(money))}
    options["none"] = "No amount is this item's price."
    for item_id, name in item_names.items():
        questions[f"price_{item_id}"] = choice(
            f"Which amount in `amounts` is the price the supplier quotes for {name}?", options
        )
    return await d.decide("J8", state, questions, primary=["intent"], job_id=job_id)


# ---------------------------------------------------------------- J9 live call line


async def j9_call_line(
    d: Decider, line: str, recent: list[str], item_names: dict[str, str], job_id: str | None = None
) -> Decision:
    state = {"supplierLine": line, "recentTurns": recent[-6:], "items": item_names}
    targets = {f"item_{k}": v for k, v in item_names.items()}
    targets["whole_list"] = "A total for the whole list."
    questions = {
        "new_offer": noul("Does the supplier state a new price or offer in `supplierLine`?"),
        "offer_for": choice("What does the price in `supplierLine` apply to?", targets),
        "beats": noul("Does the supplier agree to beat or match the price they were given?"),
    }
    return await d.decide("J9", state, questions, primary=["new_offer"], job_id=job_id)


# ---------------------------------------------------------------- J10 quote risk terms


async def j10_terms(d: Decider, quote_text: str, job_id: str | None = None) -> dict[str, float]:
    state = {"quote": quote_text[:3000]}
    questions = {
        "conditional_price": noul("Does the price in `quote` depend on volume, timing or another condition?"),
        "delivery_fee_mentioned": noul("Does `quote` mention a delivery or shipping fee?"),
        "availability_risk": noul("Does `quote` say that availability is uncertain or items are backordered?"),
    }
    decision = await d.decide("J10", state, questions, job_id=job_id)
    return {k: float(v["noul"]) for k, v in decision.answers.items() if "noul" in v}


# ---------------------------------------------------------------- J11 forecast-miss cause


MISS_CAUSES = {
    # The most common, least harmful answer goes last (first-option bias, see module docstring).
    "supply_shortage": "Sales were limited by our own supply (backorder, short delivery), not by demand.",
    "customer_lost": "A regular customer stopped ordering this product.",
    "customer_gained": "A new customer started ordering this product.",
    "product_substitution": "Buyers moved to a newly launched product, so this product sells less from now on.",
    "lasting_shift_up": "Demand moved to a higher level that will continue.",
    "lasting_shift_down": "Demand moved to a lower level that will continue.",
    "one_time_event": "A one-time order or event that will not repeat.",
    "noise": "Normal week-to-week variation with no clear cause.",
}


async def j11_miss_cause(d: Decider, state: dict[str, Any], job_id: str | None = None) -> Decision:
    """state: {item, week, miss (word bucket), previousWeek (word bucket), facts: [str]} - no raw math."""
    questions = {
        "cause": choice(
            "The sales of `item` in `week` were outside the forecast range (`miss`). "
            "Using only `facts` and `previousWeek`, what is the most likely cause?",
            MISS_CAUSES,
        )
    }
    return await d.decide("J11", state, questions, primary=["cause"], job_id=job_id)


# ---------------------------------------------------------------- J12 owner message -> adjustment


ADJUSTMENT_KINDS = {
    "not_a_forecast_change": "The message does not change how much the business will sell.",
    "cancel_previous": "The owner takes back an earlier change they told us about (an event or order that is off).",
    "one_time_extra": "Extra units for one event or one day only.",
    "lasting_increase": "The business will sell more of the item from a date on, every week.",
    "lasting_decrease": "The business will sell less of the item from a date on, every week.",
    "pause": "The business stops selling the item for a short time, then resumes.",
    "discontinue": "The business stops selling the item for good.",
}


async def j12_owner_message(
    d: Decider, message: str, catalog: dict[str, str], quantities: list[str], dates: list[str],
    job_id: str | None = None,
) -> Decision:
    state = {"message": message, "products": list(catalog.values()), "quantityPhrases": quantities,
             "datePhrases": dates}
    items = {name: None for name in catalog.values()}
    items["none of these"] = "The message is not about one of these products."
    questions: dict[str, Any] = {
        "item": choice("Which product in `products` is the owner's `message` about?", items),
        "kind": choice("What change to future sales does the owner's `message` describe?", ADJUSTMENT_KINDS),
    }
    if quantities:
        options = {q: None for q in quantities}
        options["not stated"] = "The message gives no quantity for the change."
        questions["quantity"] = choice(
            "Which phrase in `quantityPhrases` is the number of units the change adds or removes?", options)
    if dates:
        options = {p: None for p in dates}
        options["not stated"] = "The message gives no date."
        questions["date"] = choice("Which phrase in `datePhrases` says when the change starts?", options)
    return await d.decide("J12", state, questions, primary=["item", "kind"], job_id=job_id)
