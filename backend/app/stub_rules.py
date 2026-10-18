"""Deterministic stand-ins for Jev decisions in fixture mode and tests.

They read the same state and questions that the live calls send, so the code paths
around them are the real ones. They are not a model of Jev's accuracy.
"""

from __future__ import annotations

import re
from typing import Any

from app.jev import choice_answer

SUPPLY_WORDS = re.compile(
    r"glove|box|liner|parchment|bag|tissue|wrap|cup|lid|napkin|foil|container|sheet|label|tape", re.I
)
CLEANING_WORDS = re.compile(r"sanitiz|cleaner|towel|soap|degreaser|trash", re.I)
FOOD_WORDS = re.compile(
    r"bun|bread|croissant|cake|loaf|boule|flour|sugar|butter|yeast|egg|milk|roll\b|pie|muffin", re.I
)


def j1_triage(state: dict, questions: dict) -> dict:
    text = f"{state.get('itemName', '')} {state.get('description', '')}"
    if FOOD_WORDS.search(text) and not SUPPLY_WORDS.search(state.get("itemName", "")):
        category = "food_ingredient"
    elif CLEANING_WORDS.search(text):
        category = "cleaning_safety"
    elif SUPPLY_WORDS.search(text):
        category = "packaging_disposables"
    else:
        category = "other"
    options = list(questions["category"]["criteria"])
    online = 0.9 if category in ("packaging_disposables", "cleaning_safety") else 0.1
    return {
        "category": choice_answer(options, category, 0.9),
        "online_buyable": {"type": "noul", "noul": online},
    }


PAGE_RULES = [
    # A review-and-pay page can show no card field yet (WebstaurantStore step 2): fail safe. Its
    # name also sits in step 1's progress bar, so match what only the review page shows.
    ("payment_step", re.compile(r"card number|payment method|cvv|security code|pay now|place order|"
                                r"payment information|change shipping address|edit billing address", re.I)),
    # "This form is protected by reCAPTCHA" is a footer notice on normal forms, not a challenge.
    ("bot_check", re.compile(r"(?<!protected by re)captcha|verify you are human|unusual traffic", re.I)),
    ("login_wall", re.compile(r"sign in to continue|log in to see prices|create an account to", re.I)),
    ("shipping_step", re.compile(r"shipping address|delivery address|ship to", re.I)),
    ("cart", re.compile(r"your cart|shopping cart|subtotal", re.I)),
    ("out_of_stock", re.compile(r"out of stock|unavailable", re.I)),
    ("product_page", re.compile(r"add to cart", re.I)),
    ("search_results", re.compile(r"results for", re.I)),
]


def j6_page_state(state: dict, questions: dict) -> dict:
    text = state.get("visibleText", "")
    page = "other"
    for name, pattern in PAGE_RULES:
        if pattern.search(text):
            page = name
            break
    options = list(questions["page"]["criteria"])
    return {"page": choice_answer(options, page, 0.92)}


TOTAL_LABELS = {
    "subtotal": re.compile(r"sub\s*-?total|items total|merchandise", re.I),
    "shipping": re.compile(r"shipping|delivery|freight", re.I),
    "tax": re.compile(r"\btax", re.I),
    "total": re.compile(r"^(order |grand |estimated )?total", re.I),
}


def j7_totals(state: dict, questions: dict) -> dict:
    answers: dict[str, Any] = {}
    candidates = state.get("candidates", [])
    for field, q in questions.items():
        options = list(q["criteria"])
        pick = "none"
        for cand in candidates:
            if TOTAL_LABELS[field].search(cand["label"].strip()):
                pick = cand["id"]
                break
        answers[field] = choice_answer(options, pick, 0.95)
    return answers


# Fixture lanes register the "true" J5 answers for their listings, keyed by title.
FIXTURE_J5: dict[str, dict[str, Any]] = {}


def j5_listing(state: dict, questions: dict) -> dict:
    expected = FIXTURE_J5.get(state.get("listing", {}).get("title", ""), {})
    probs = expected.get("matchProbs", {"0": 0.02, "1": 0.08, "2": 0.90})
    answers: dict[str, Any] = {
        "match": {
            "type": "score",
            "score": sum(int(k) * v for k, v in probs.items()),
            "probabilities": probs,
            "confidence": 0.9,
        },
        "in_stock": {"type": "noul", "noul": expected.get("inStock", 0.9)},
    }
    must = expected.get("mustHave", [])
    for key in questions:
        if key.startswith("must_"):
            i = int(key.split("_", 1)[1])
            answers[key] = {"type": "noul", "noul": must[i] if i < len(must) else 0.9}
    if "pack" in questions:
        options = list(questions["pack"]["criteria"])
        wanted = str(expected.get("pack", "")).lower()
        pick = next((o for o in options if o.lower() == wanted), options[0])
        answers["pack"] = choice_answer(options, pick, 0.93)
    return answers


NOT_A_SELLER = re.compile(r"directory|marketplace|blog|review|not a seller", re.I)


def j3_supplier(state: dict, questions: dict) -> dict:
    seller = 0.1 if NOT_A_SELLER.search(state.get("notes", "")) else 0.92
    return {
        "sells_category": {"type": "noul", "noul": 0.9},
        "prices_without_login": {"type": "noul", "noul": 0.85},
        "is_supplier": {"type": "noul", "noul": seller},
        "ships_to_business": {"type": "noul", "noul": 0.9},
    }


TERM_RULES = {
    "conditional_price": re.compile(r"only if|if you order|minimum of|valid (until|through)|this week only", re.I),
    "delivery_fee_mentioned": re.compile(r"deliver|shipping|freight", re.I),
    "availability_risk": re.compile(r"backorder|out of stock|subject to availability|may not have", re.I),
}


def j10_terms(state: dict, questions: dict) -> dict:
    text = state.get("quote", "")
    return {k: {"type": "noul", "noul": 0.9 if TERM_RULES[k].search(text) else 0.08}
            for k in questions if k in TERM_RULES}


RULES = {
    "J1": j1_triage, "J3": j3_supplier, "J5": j5_listing, "J6": j6_page_state, "J7": j7_totals,
    "J10": j10_terms,
}


def j11_miss_cause(state: dict, questions: dict) -> dict:
    facts = " ".join(state.get("facts", [])).lower()
    miss = state.get("miss", "")
    prev = state.get("previousWeek", "")
    below = "below" in miss
    if "backorder" in facts and below:
        cause, p = "supply_shortage", 0.88
    elif "stopped ordering" in facts and below:
        cause, p = "customer_lost", 0.86
    elif "started selling" in facts and below:
        cause, p = "product_substitution", 0.82
    elif "first order" in facts and not below:
        cause, p = "customer_gained", 0.84
    elif ("below" in prev and below) or ("above" in prev and not below):
        cause, p = ("lasting_shift_down" if below else "lasting_shift_up"), 0.83
    elif "far" in miss and not below:
        cause, p = "one_time_event", 0.7  # confirm band: ask the owner
    else:
        cause, p = "noise", 0.81
    return {"cause": choice_answer(list(questions["cause"]["criteria"]), cause, p)}


KIND_WORDS = [
    ("cancel_previous", re.compile(r"\b(cancel|forget|never ?mind|scratch that|call off|not doing)\b", re.I)),
    ("discontinue", re.compile(r"discontinu|stop (selling|making|carrying)|no longer|drop (the|our)", re.I)),
    ("pause", re.compile(r"pause|closed|not (selling|making) .* (this|next) week", re.I)),
    ("lasting_increase", re.compile(r"every week|each week|going forward|from now on|new (regular|standing) order", re.I)),
    ("one_time_extra", re.compile(r"wedding|event|catering|party|festival|one[- ]time|extra", re.I)),
    ("lasting_decrease", re.compile(r"less|fewer|cut back|reduce", re.I)),
]


def j12_owner_message(state: dict, questions: dict) -> dict:
    text = state.get("message", "")
    low = text.lower()
    items = list(questions["item"]["criteria"])
    item = "none of these"
    for name in items:
        head = name.split(",")[0].lower()
        words = [w for w in re.findall(r"[a-z]+", head) if len(w) > 3]
        if head in low or any(re.search(rf"\b{w}s?\b", low) for w in words[-2:]):
            item = name
            break
    kind = next((k for k, rx in KIND_WORDS if rx.search(text)), "not_a_forecast_change")
    answers = {
        "item": choice_answer(items, item, 0.9),
        "kind": choice_answer(list(questions["kind"]["criteria"]), kind, 0.88),
    }
    if "quantity" in questions:
        opts = list(questions["quantity"]["criteria"])
        answers["quantity"] = choice_answer(opts, opts[0], 0.85)
    if "date" in questions:
        opts = list(questions["date"]["criteria"])
        answers["date"] = choice_answer(opts, opts[0], 0.85)
    return answers


RULES["J11"] = j11_miss_cause
RULES["J12"] = j12_owner_message
