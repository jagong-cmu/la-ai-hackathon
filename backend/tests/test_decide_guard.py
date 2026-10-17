import pytest

from app.contracts import PageInput, PageSnapshot
from app.decide import Stakes
from app.guard import check_page
from app.jev import JevError, JevResult, choice, noul


class FakeJev:
    """Returns fixed answers, or raises."""

    def __init__(self, answers=None, error=False):
        self.answers = answers
        self.error = error
        self.live = True

    async def ask(self, purpose, state, questions, *, timeout):
        if self.error:
            raise JevError("down")
        return JevResult(self.answers, 12.0, 100, "jev")


def page_answer(p_payment: float, pick: str) -> dict:
    probs = {"payment_step": p_payment, "cart": 1 - p_payment}
    return {"page": {"type": "choice", "choice": pick, "probabilities": probs, "confidence": 0.5}}


async def test_act_band_uses_jev(decider):
    decider.jev = FakeJev({"q": {"type": "noul", "noul": 0.97}})
    d = await decider.decide("J9", {"x": 1}, {"q": noul("?")})
    assert d.band == "act" and not d.escalated


async def test_confirm_band_escalates_and_falls_back_when_step5_is_down(decider):
    decider.jev = FakeJev({"q": {"type": "noul", "noul": 0.7}})  # confidence 0.4 -> below confirm 0.5
    d = await decider.decide("J9", {"x": 1}, {"q": noul("?")})
    assert d.band == "unsure"
    decider.jev = FakeJev({"q": {"type": "noul", "noul": 0.85}})  # confidence 0.7 -> confirm band
    d = await decider.decide("J9", {"x": 1}, {"q": noul("?")})
    assert d.band == "unsure" and "step5 failed" in " ".join(d.notes)  # no Step 5 key in tests


async def test_safety_never_escalates_and_fails_closed(decider):
    decider.jev = FakeJev(error=True)
    d = await decider.decide("J6", {}, {"page": choice("?", {"a": None, "b": None})},
                             stakes=Stakes.safety, safe_default=None)
    assert d.band == "safe_default"


async def test_decisions_are_logged(decider, db):
    decider.jev = FakeJev({"q": {"type": "noul", "noul": 0.99}})
    await decider.decide("J10", {"x": 1}, {"q": noul("?")}, job_id="job_x")
    rows = db.decisions("J10")
    assert rows and rows[0]["job_id"] == "job_x" and rows[0]["band"] == "act"


@pytest.mark.parametrize(
    "inputs",
    [
        [PageInput(autocomplete="cc-number")],
        [PageInput(name="cardNumber")],
        [PageInput(label="Security code (CVV)")],
    ],
)
async def test_dom_card_fields_stop_without_a_model(decider, settings, inputs):
    decider.jev = FakeJev(error=True)  # the model is not even asked
    v = await check_page(decider, PageSnapshot(url="u", visible_text="", inputs=inputs), settings.thresholds)
    assert v.stop and v.reason == "dom_card_field"


async def test_guard_stops_on_low_payment_probability(decider, settings):
    decider.jev = FakeJev(page_answer(0.25, "cart"))  # threshold 0.2
    v = await check_page(decider, PageSnapshot(url="u", visible_text="Your cart"), settings.thresholds)
    assert v.stop and v.reason == "payment_step"


async def test_guard_passes_cart_and_fails_closed_when_jev_is_down(decider, settings):
    decider.jev = FakeJev(page_answer(0.05, "cart"))
    v = await check_page(decider, PageSnapshot(url="u", visible_text="Your cart"), settings.thresholds)
    assert not v.stop and v.page_state == "cart"
    decider.jev = FakeJev(error=True)
    v = await check_page(decider, PageSnapshot(url="u", visible_text="Your cart"), settings.thresholds)
    assert v.stop and v.reason == "guard_unavailable"


async def test_stub_guard_detects_bot_check(decider, settings):
    v = await check_page(decider, PageSnapshot(url="u", visible_text="Please verify you are human"),
                         settings.thresholds)
    assert not v.stop and v.blocked == "bot_check"
