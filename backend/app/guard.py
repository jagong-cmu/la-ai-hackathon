"""Payment guard (spec §5). Lanes call it after every navigation and before every
click or type action. It fails closed: when in doubt, stop.

Layer 1: DOM check for card fields (no model involved).
Layer 2: J6 page state; P(payment step) >= threshold stops.
Layer 3: J6 unavailable -> stop.
The browser and the agent prompt never receive card data; that is the last layer.
"""

from __future__ import annotations

import re

from app.config import Thresholds
from app.contracts import GuardVerdict, PageSnapshot
from app.decide import Decider
from app.judgments import j6_page_state

CARD_AUTOCOMPLETE = {"cc-number", "cc-exp", "cc-exp-month", "cc-exp-year", "cc-csc", "cc-name", "cc-type"}
CARD_FIELD = re.compile(r"card.?(number|no\b)|\bcvv\b|\bcvc\b|security.?code|cc-?num|expir", re.I)
BLOCKING_STATES = {"login_wall", "bot_check", "out_of_stock"}


class GuardStop(Exception):
    def __init__(self, verdict: GuardVerdict):
        super().__init__(verdict.reason)
        self.verdict = verdict


def dom_card_fields(page: PageSnapshot) -> bool:
    for field in page.inputs:
        if (field.autocomplete or "").lower() in CARD_AUTOCOMPLETE:
            return True
        for text in (field.name, field.id, field.label):
            if text and CARD_FIELD.search(text):
                return True
    return False


async def check_page(
    decider: Decider, page: PageSnapshot, thresholds: Thresholds, job_id: str | None = None
) -> GuardVerdict:
    if dom_card_fields(page):
        return GuardVerdict(stop=True, reason="dom_card_field", page_state="payment_step", p_payment=1.0)
    decision = await j6_page_state(decider, page, job_id=job_id)
    if decision.band == "safe_default" or "page" not in decision.answers:
        return GuardVerdict(stop=True, reason="guard_unavailable")
    answer = decision.answers["page"]
    p_payment = float(answer.get("probabilities", {}).get("payment_step", 0.0))
    page_state = answer.get("choice")
    if page_state == "payment_step" or p_payment >= thresholds.payment_stop:
        return GuardVerdict(stop=True, reason="payment_step", page_state=page_state, p_payment=p_payment)
    blocked = page_state if page_state in BLOCKING_STATES else None
    return GuardVerdict(stop=False, page_state=page_state, p_payment=p_payment, blocked=blocked)
