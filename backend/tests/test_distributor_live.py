"""LiveDistributor against a fake Photon service (httpx.MockTransport) and scripted inbox events."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.contracts import Assignment, DistributorRef, Plan, ShoppingItem, SupplierOffers
from app.decide import Decider
from app.distributor.base import DistributorContext
from app.distributor.live import (
    LiveDistributor,
    LiveDistributorConfig,
    lead_time_days,
    select_price,
)
from app.distributor.registry import channel_for
from app.jev import choice_answer

ITEMS = [
    ShoppingItem(id="gloves", name="Nitrile gloves, medium", quantity=2000, unit="glove"),
    ShoppingItem(id="boxes", name="White bakery boxes", quantity=500, unit="box"),
]
BEST_TOTAL = 410.00


def _plan() -> Plan:
    return Plan(
        plan_id="p1", kind="split", label="best web", carts=[], total=BEST_TOTAL, status="estimated",
        assignments=[
            Assignment(item_id="gloves", supplier_id="uline", sku="G1", pack_quantity=20, line_cost=260.0, source="web"),
            Assignment(item_id="boxes", supplier_id="webstaurant", sku="B1", pack_quantity=5, line_cost=150.0, source="web"),
        ],
    )


def _j9_rule(state: dict, questions: dict) -> dict:
    """Deterministic J9 for the tests: an amount + 'can do' is an offer for the whole list."""
    line = state["supplierLine"].lower()
    offer = "$" in line or "dollars" in line
    target = "whole_list" if "list" in line or "everything" in line else "item_gloves"
    return {
        "new_offer": {"type": "noul", "noul": 0.95 if offer else 0.03},
        "offer_for": choice_answer(list(questions["offer_for"]["criteria"]), target, 0.93),
        "beats": {"type": "noul", "noul": 0.9 if offer else 0.1},
    }


class FakePhoton:
    """Records requests; on POST /calls it plays a scripted list of webhook events into the inbox."""

    def __init__(self, script: list[dict[str, Any]] | None = None, call_status: int = 200,
                 sms_status: int = 200) -> None:
        self.script = script or []
        self.call_status = call_status
        self.sms_status = sms_status
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.ctx: DistributorContext | None = None
        self._tasks: list[asyncio.Task] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append((request.url.path, body))
        if request.url.path == "/sms":
            return httpx.Response(self.sms_status, json={"messageId": f"m{len(self.requests)}"})
        if request.url.path == "/calls":
            if self.call_status != 200:
                return httpx.Response(self.call_status, json={"error": "down"})
            n = sum(1 for p, _ in self.requests if p == "/calls")
            self._tasks.append(asyncio.get_running_loop().create_task(self._play(f"call{n}")))
            return httpx.Response(200, json={"callId": f"call{n}", "status": "dialing"})
        return httpx.Response(404)

    async def _play(self, call_id: str) -> None:
        assert self.ctx is not None
        for i, event in enumerate(self.script):
            await asyncio.sleep(event.pop("_after", 0.01))
            await self.ctx.inbox.put({"provider": "photon", "eventId": f"{call_id}-e{i}", "jobId": "job_t",
                                      "callId": call_id, **event})


def _ctx(settings: Settings, decider: Decider, events: list[tuple[str, dict]],
         offers: list[SupplierOffers]) -> DistributorContext:
    async def emit(type_: str, data: dict) -> None:
        events.append((type_, data))

    async def on_offer(o: SupplierOffers) -> None:
        offers.append(o)

    ready = asyncio.Event()
    ready.set()
    return DistributorContext(
        job_id="job_t", distributor=DistributorRef(id="dist", name="Larkmoor (fictional)", phone="+15550000000"),
        items=ITEMS, settings=settings, decider=decider, emit=emit, best_plan=_plan,
        first_plan_ready=ready, on_offer=on_offer, started_at=asyncio.get_running_loop().time(),
    )


def _sms_first(settings: Settings) -> Settings:
    return settings.model_copy(update={"distributor_flow": "sms_first"})


def _config(**kw: Any) -> LiveDistributorConfig:
    base = dict(_env_file=None, photon_service_url="http://photon.test", demo_distributor_phone="+15551234567",
                call_connect_timeout_s=0.3, call_max_s=2.0, post_call_grace_s=0.3, call_redials=1)
    base.update(kw)
    return LiveDistributorConfig(**base)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """These tests cover a call about both items; the one-item demo default has its own test."""
    return settings.model_copy(update={"distributor_call_max_items": 2})


@pytest.fixture
def j9_decider(decider: Decider) -> Decider:
    decider.jev.stub.rules["J9"] = _j9_rule
    return decider


async def test_sms_first_call_offer_flows_through_j9_and_code_selection(settings: Settings, j9_decider: Decider) -> None:
    script = [
        {"type": "call_started"},
        {"type": "transcript_line", "seq": 1, "speaker": "agent",
         "text": "Hi, this is an AI assistant calling for Hearthloaf Bakeries. Is now a good time?"},
        {"type": "transcript_line", "seq": 2, "speaker": "user", "text": "Sure, go ahead."},
        {"type": "transcript_line", "seq": 2, "speaker": "user", "text": "Sure, go ahead."},  # duplicate seq
        {"type": "transcript_line", "seq": 3, "speaker": "agent",
         "text": "The best price we found is $410.00 delivered. Can you beat it?"},
        {"type": "transcript_line", "seq": 4, "speaker": "user",
         "text": "For your $410 list I can do 395 dollars for everything, free delivery on Thursday."},
        {"type": "call_ended", "outcome": "completed"},
    ]
    fake = FakePhoton(script)
    events: list[tuple[str, dict]] = []
    offers: list[SupplierOffers] = []
    ctx = _ctx(_sms_first(settings), j9_decider, events, offers)
    fake.ctx = ctx
    channel = LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler))

    result = await asyncio.wait_for(channel.run(ctx), timeout=5)

    types = [t for t, _ in events]
    assert types == [
        "message_sent", "reply_received", "call_started",
        "transcript_line", "transcript_line", "transcript_line", "transcript_line",
        "call_offer", "call_ended", "message_sent",
    ]
    assert events[0][1]["text"].startswith("Hi, this is an AI assistant for Hearthloaf Bakeries.")
    assert events[1][1]["intent"] == "no_reply"
    assert [d["speaker"] for t, d in events if t == "transcript_line"] == ["agent", "supplier", "agent", "supplier"]
    offer_event = next(d for t, d in events if t == "call_offer")
    assert offer_event["price"] == 395.0 and offer_event["itemId"] is None and offer_event["beats"] is True
    assert events[-2][1]["outcome"] == "quote"
    assert "not an order" in events[-1][1]["text"]

    assert result is not None and result.source == "call" and result.bundle_total == 395.0
    assert result.quoted_delivery_fee == 0.0 and result.quoted_lead_time_days is not None
    assert result.jev_terms["delivery_fee_mentioned"] > 0.5
    assert result.evidence.transcript_url == "/evidence/job_t/distributor/call.txt"
    assert len(offers) == 2 and offers[-1] is result  # live re-plan on the offer, then the final quote

    paths = [p for p, _ in fake.requests]
    assert paths == ["/sms", "/calls", "/sms"]
    call_body = fake.requests[1][1]
    assert call_body["to"] == "+15551234567"  # the demo phone, never the distributor's own number
    assert call_body["openingContext"]["bestTotal"] == BEST_TOTAL
    assert [i["id"] for i in call_body["openingContext"]["items"]] == ["gloves", "boxes"]


async def test_post_call_transcript_is_used_when_no_live_lines(settings: Settings, j9_decider: Decider) -> None:
    script = [
        {"type": "call_started"},
        {"type": "transcript_line", "seq": 0, "speaker": "agent",
         "text": "Hi, this is an AI assistant for Hearthloaf Bakeries."},
        {"type": "call_ended", "outcome": "completed", "final": False},
        {"type": "call_ended", "outcome": "completed", "transcript": [
            {"seq": 0, "role": "agent", "message": "Hi, this is an AI assistant for Hearthloaf Bakeries."},
            {"seq": 1, "role": "user", "message": "Gloves I can do at $240."},
        ]},
    ]
    fake = FakePhoton(script)
    events: list[tuple[str, dict]] = []
    offers: list[SupplierOffers] = []
    ctx = _ctx(settings, j9_decider, events, offers)
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    assert result is not None and result.status == "partial"
    assert [(p.item_id, p.pack_price) for p in result.products] == [("gloves", 240.0)]
    offer_event = next(d for t, d in events if t == "call_offer")
    assert offer_event["itemId"] == "gloves" and offer_event["beats"] is True
    lines = [d["text"] for t, d in events if t == "transcript_line"]
    assert len(lines) == 2  # the live line is not repeated from the post-call transcript


async def test_live_tool_lines_are_shown_and_prices_come_from_the_exact_transcript(
        settings: Settings, j9_decider: Decider) -> None:
    script = [
        {"type": "call_started"},
        {"type": "transcript_line", "seq": 0, "speaker": "agent", "text": "Hi, this is an AI assistant.", "live": True},
        # The agent's own note of the reply: a paraphrase with a wrong amount. It is never priced.
        {"type": "transcript_line", "seq": 1, "speaker": "supplier", "text": "Gloves about $250.", "live": True},
        {"type": "transcript_line", "seq": 2, "speaker": "supplier", "text": "  ", "live": True},
        {"type": "call_ended", "outcome": "completed", "transcript": [
            {"seq": 0, "role": "agent", "message": "Hi, this is an AI assistant."},
            {"seq": 1, "role": "user", "message": "Gloves I can do at $240."},
        ]},
    ]
    fake = FakePhoton(script)
    events: list[tuple[str, dict]] = []
    offers: list[SupplierOffers] = []
    ctx = _ctx(settings, j9_decider, events, offers)
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)

    lines = [(d["speaker"], d["text"], d.get("live")) for t, d in events if t == "transcript_line"]
    assert lines == [("agent", "Hi, this is an AI assistant.", True), ("supplier", "Gloves about $250.", True)]
    offer_events = [d for t, d in events if t == "call_offer"]
    assert [d["price"] for d in offer_events] == [240.0]  # from the exact post-call line only
    assert result is not None and [(p.item_id, p.pack_price) for p in result.products] == [("gloves", 240.0)]


async def test_call_never_connects_degrades_to_sms_only(settings: Settings, j9_decider: Decider) -> None:
    fake = FakePhoton(script=[])  # the call is placed, but no call_started ever arrives
    events: list[tuple[str, dict]] = []
    offers: list[SupplierOffers] = []
    ctx = _ctx(_sms_first(settings), j9_decider, events, offers)
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    assert result is None and offers == []
    assert [p for p, _ in fake.requests] == ["/sms", "/calls", "/calls"]  # one redial
    types = [t for t, _ in events]
    assert "call_started" not in types
    assert ("call_ended", {"outcome": "no_answer"}) in events
    assert ("dependency_degraded", {"dep": "phone", "reason": "no_answer"}) in events


async def test_photon_service_down_degrades_without_hanging(settings: Settings, j9_decider: Decider) -> None:
    fake = FakePhoton(call_status=503, sms_status=503)
    events: list[tuple[str, dict]] = []
    ctx = _ctx(_sms_first(settings), j9_decider, events, [])
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(photon_sms_retries=0), transport=httpx.MockTransport(fake.handler)).run(ctx),
        timeout=5)
    assert result is None
    reasons = [d["reason"] for t, d in events if t == "dependency_degraded"]
    assert reasons == ["sms_failed", "call_failed"]


async def test_sms_quote_skips_the_call(settings: Settings, j9_decider: Decider) -> None:
    def j8(state: dict, questions: dict) -> dict:
        answers = {"intent": choice_answer(list(questions["intent"]["criteria"]), "quote", 0.95)}
        for i, key in enumerate(k for k in questions if k.startswith("price_")):
            answers[key] = choice_answer(list(questions[key]["criteria"]), f"c{i}", 0.95)
        return answers

    j9_decider.jev.stub.rules["J8"] = j8
    fake = FakePhoton()
    events: list[tuple[str, dict]] = []
    offers: list[SupplierOffers] = []
    ctx = _ctx(_sms_first(settings), j9_decider, events, offers)
    fake.ctx = ctx
    await ctx.inbox.put({"provider": "photon", "eventId": "s1", "jobId": "job_t", "type": "sms_in",
                         "text": "Gloves $250.00\nBoxes $140.00\nDelivery is free, 2 days."})
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    assert result is not None and result.source == "sms" and result.status == "ok"
    assert {p.item_id: p.pack_price for p in result.products} == {"gloves": 250.0, "boxes": 140.0}
    assert result.quoted_delivery_fee == 0.0 and result.quoted_lead_time_days == 2
    assert [p for p, _ in fake.requests] == ["/sms"]


async def test_no_demo_phone_never_contacts_anyone(settings: Settings, j9_decider: Decider) -> None:
    fake = FakePhoton()
    events: list[tuple[str, dict]] = []
    ctx = _ctx(settings, j9_decider, events, [])
    result = await LiveDistributor(_config(demo_distributor_phone=""),
                                   transport=httpx.MockTransport(fake.handler)).run(ctx)
    assert result is None and fake.requests == []
    assert events[0][0] == "dependency_degraded"


def test_code_helpers() -> None:
    assert select_price("For your $410 list I can do $395.", 410.0) == 395.0
    assert select_price("I can match $410.", 410.0) == 410.0
    assert select_price("Three ninety five", 410.0) is None
    assert select_price("1,250 dollars total", None) == 1250.0
    assert lead_time_days("we deliver in 3 business days") == 3


def test_live_channel_is_registered() -> None:
    import app.distributor.live_setup  # noqa: F401

    class Modes:
        def get(self, dep: str) -> str:
            return "live"

    assert isinstance(channel_for(Modes()), LiveDistributor)  # type: ignore[arg-type]


async def test_call_first_calls_before_any_text(settings: Settings, j9_decider: Decider) -> None:
    """Default flow: the agent calls the distributor first; the only text is the recap after the call."""
    assert settings.distributor_flow == "call_first"
    script = [
        {"type": "call_started"},
        {"type": "transcript_line", "seq": 1, "speaker": "agent",
         "text": "Hi, this is an AI assistant calling for Hearthloaf Bakeries. Is now a good time?"},
        {"type": "transcript_line", "seq": 2, "speaker": "user",
         "text": "I can do 395 dollars for everything, free delivery on Thursday."},
        {"type": "call_ended", "outcome": "completed"},
    ]
    fake = FakePhoton(script)
    events: list[tuple[str, dict]] = []
    offers: list[SupplierOffers] = []
    ctx = _ctx(settings, j9_decider, events, offers)
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    types = [t for t, _ in events]
    assert types[0] == "call_started" and "reply_received" not in types
    assert types[-1] == "message_sent" and "not an order" in events[-1][1]["text"]  # the recap
    assert [p for p, _ in fake.requests] == ["/calls", "/sms"]
    assert result is not None and result.bundle_total == 395.0


async def test_call_first_no_answer_redials_once_and_sends_nothing(settings: Settings, j9_decider: Decider) -> None:
    fake = FakePhoton(script=[])
    events: list[tuple[str, dict]] = []
    ctx = _ctx(settings, j9_decider, events, [])
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    assert result is None
    assert [p for p, _ in fake.requests] == ["/calls", "/calls"]  # one redial, no texts


def test_spoken_price_basis_from_the_first_real_call() -> None:
    """Lines from the first real test call (Oct 17). Per-unit prices are multiplied in code."""
    from app.distributor.live import spoken_price
    assert spoken_price("It's around 50 cents a glove.", 716.34, "glove") == (0.50, "per_unit")
    assert spoken_price("Uh, that's around a $1.60 per box.", 716.34, "box") == (1.60, "per_unit")
    assert spoken_price("Um, 26 cents a bag.", 716.34, "bag") == (0.26, "per_unit")
    assert spoken_price("We can do $700.", 716.34, None) == (700.0, "total")
    assert spoken_price("Gloves are $12 a box.", None, "glove") == (12.0, "per_pack")
    assert spoken_price("$40 per case", None, "sheet") == (40.0, "per_pack")
    assert spoken_price("about 395 dollars for everything", 410.0, None) == (395.0, "total")
    assert spoken_price("free delivery", None, None) is None


async def test_per_unit_price_becomes_a_line_total(settings: Settings, j9_decider: Decider) -> None:
    def j9(state: dict, questions: dict) -> dict:
        return {
            "new_offer": {"type": "noul", "noul": 0.95},
            "offer_for": choice_answer(list(questions["offer_for"]["criteria"]), "item_gloves", 0.95),
            "beats": {"type": "noul", "noul": 0.5},
        }

    j9_decider.jev.stub.rules["J9"] = j9
    script = [
        {"type": "call_started"},
        {"type": "transcript_line", "seq": 1, "speaker": "user", "text": "It's around 10 cents a glove."},
        {"type": "call_ended", "outcome": "completed"},
    ]
    fake = FakePhoton(script)
    events: list[tuple[str, dict]] = []
    ctx = _ctx(settings, j9_decider, events, [])
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    offer = next(d for t, d in events if t == "call_offer")
    assert offer["basis"] == "per_unit" and offer["unitPrice"] == 0.10
    assert offer["price"] == 200.0  # 0.10 x 2,000 gloves, computed in code
    assert offer["beats"] is True  # $200 beats the $260 web line for gloves
    assert result is not None and [(p.item_id, p.pack_price) for p in result.products] == [("gloves", 200.0)]


def test_call_scope_and_total_split() -> None:
    from app.distributor.base import allocate_total, call_scope
    plan = _plan()  # gloves $260, boxes $150
    assert [i.id for i in call_scope(ITEMS, plan, 1)] == ["gloves"]
    assert [i.id for i in call_scope(ITEMS, plan, 2)] == ["gloves", "boxes"]
    assert [i.id for i in call_scope(ITEMS, plan, 0)] == ["gloves", "boxes"]
    parts = allocate_total(395.0, {"gloves": 260.0, "boxes": 150.0})
    assert parts == {"gloves": 250.49, "boxes": 144.51} and round(sum(parts.values()), 2) == 395.0


async def test_short_call_splits_a_total_over_its_items(settings: Settings, j9_decider: Decider) -> None:
    """With 1 item in scope, '$240 for everything' becomes that item's price, not a whole-list bundle."""
    scoped = settings.model_copy(update={"distributor_call_max_items": 1})
    script = [
        {"type": "call_started"},
        {"type": "transcript_line", "seq": 1, "speaker": "user", "text": "I can do $240 for everything."},
        {"type": "call_ended", "outcome": "completed"},
    ]
    fake = FakePhoton(script)
    events: list[tuple[str, dict]] = []
    ctx = _ctx(scoped, j9_decider, events, [])
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    scope = next(d for t, d in events if t == "call_scope")
    assert scope == {"itemIds": ["gloves"], "anchor": 260.0}
    call_body = next(b for p, b in fake.requests if p == "/calls")
    assert [i["id"] for i in call_body["openingContext"]["items"]] == ["gloves"]
    assert call_body["openingContext"]["bestTotal"] == 260.0
    assert result is not None and result.bundle_total is None
    assert [(p.item_id, p.pack_price) for p in result.products] == [("gloves", 240.0)]


async def test_real_call_j9_answers_confirm_both_item_prices(settings: Settings, j9_decider: Decider) -> None:
    """Jev answers from the relay-test call: new_offer 0.89 and 0.54, offer_for 1.0. Both prices are
    offers for a known item, so both are confirmed (before, |2p-1| < 0.8 made them 'unsure')."""
    real = {"box": ("item_boxes", 0.89), "glove": ("item_gloves", 0.54)}

    def j9(state: dict, questions: dict) -> dict:
        line = state["supplierLine"].lower()
        target, p = next((v for k, v in real.items() if k in line), ("whole_list", 0.02))
        return {
            "new_offer": {"type": "noul", "noul": p},
            "offer_for": choice_answer(list(questions["offer_for"]["criteria"]), target, 1.0),
            "beats": {"type": "noul", "noul": 0.05},
        }

    j9_decider.jev.stub.rules["J9"] = j9
    script = [
        {"type": "call_started"},
        {"type": "transcript_line", "seq": 1, "speaker": "user", "text": "Um, that will be around a $1.50 per box."},
        {"type": "transcript_line", "seq": 2, "speaker": "user", "text": "25 cents a glove."},
        {"type": "call_ended", "outcome": "completed"},
    ]
    fake = FakePhoton(script)
    events: list[tuple[str, dict]] = []
    ctx = _ctx(settings, j9_decider, events, [])
    fake.ctx = ctx
    result = await asyncio.wait_for(
        LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)
    offers = [d for t, d in events if t == "call_offer"]
    assert [o["confirmed"] for o in offers] == [True, True]
    assert result is not None
    assert sorted((p.item_id, p.pack_price) for p in result.products) == [("boxes", 750.0), ("gloves", 500.0)]
