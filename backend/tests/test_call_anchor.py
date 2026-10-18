"""The price that the distributor call quotes: the best web plan, else the past bill prices."""

from __future__ import annotations

import asyncio
import dataclasses

import httpx

from app.config import Settings
from app.contracts import ShoppingItem, SupplierOffers
from app.decide import Decider
from app.distributor.base import call_anchor
from app.distributor.fixture import FixtureDistributor
from app.distributor.live import LiveDistributor
from tests.test_distributor_live import FakePhoton, _config, _ctx, _j9_rule, _plan

PAST_ITEMS = [
    ShoppingItem(id="gloves", name="Nitrile gloves, medium", quantity=2000, unit="glove", past_unit_price=0.14),
    ShoppingItem(id="boxes", name="White bakery boxes", quantity=500, unit="box", past_unit_price=0.80),
    ShoppingItem(id="liners", name="Parchment liners", quantity=1000, unit="sheet"),  # no past price
]


def _no_plan_ctx(settings: Settings, decider: Decider, events: list, offers: list):
    decider.jev.stub.rules["J9"] = _j9_rule
    return dataclasses.replace(_ctx(settings, decider, events, offers), items=PAST_ITEMS, best_plan=lambda: None)


def test_anchor_prefers_the_web_plan():
    anchor = call_anchor(PAST_ITEMS, _plan())
    assert (anchor.source, anchor.total, anchor.line_costs) == ("web_plan", 410.0, {"gloves": 260.0, "boxes": 150.0})


def test_anchor_uses_past_bill_prices_without_a_plan():
    anchor = call_anchor(PAST_ITEMS, None)
    assert (anchor.source, anchor.total, anchor.line_costs) == ("past_bills", 680.0, {"gloves": 280.0, "boxes": 400.0})


def test_no_anchor_without_a_plan_or_a_past_price():
    assert call_anchor([PAST_ITEMS[2]], None) is None


async def test_fixture_call_runs_on_past_prices_and_says_so(settings: Settings, decider: Decider):
    events: list[tuple[str, dict]] = []
    offers: list[SupplierOffers] = []
    ctx = _no_plan_ctx(settings.model_copy(update={"fixture_time_scale": 0.0}), decider, events, offers)

    result = await asyncio.wait_for(FixtureDistributor().run(ctx), timeout=5)

    scope = next(d for t, d in events if t == "call_scope")
    assert scope == {"itemIds": ["boxes"], "anchor": 400.0, "anchorSource": "past_bills"}  # 1 item: the costliest
    lines = [d["text"] for t, d in events if t == "transcript_line"]
    assert any("Last time we paid about $400.00" in line for line in lines)
    assert not any("found online" in line for line in lines)  # never claim a web price that does not exist
    assert result is not None and [p.item_id for p in result.products] == ["boxes"]
    assert not any(t == "call_skipped" for t, _ in events)


async def test_live_call_without_a_plan_is_skipped_by_default(settings: Settings, decider: Decider):
    events: list[tuple[str, dict]] = []
    fake = FakePhoton()
    ctx = _no_plan_ctx(settings, decider, events, [])
    fake.ctx = ctx

    await asyncio.wait_for(LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)

    assert ("call_skipped", {"reason": "no plan to anchor the call"}) in events
    assert not any(p == "/calls" for p, _ in fake.requests)


async def test_live_call_on_past_prices_tells_the_voice_agent(settings: Settings, decider: Decider):
    script = [{"type": "call_started"}, {"type": "call_ended", "outcome": "completed"}]
    fake = FakePhoton(script)
    ctx = _no_plan_ctx(settings.model_copy(update={"distributor_call_past_price": True}), decider, [], [])
    fake.ctx = ctx

    await asyncio.wait_for(LiveDistributor(_config(), transport=httpx.MockTransport(fake.handler)).run(ctx), timeout=5)

    body = next(b for p, b in fake.requests if p == "/calls")["openingContext"]
    assert (body["anchorSource"], body["bestTotal"]) == ("past_bills", 400.0)
    assert [i["id"] for i in body["items"]] == ["boxes"]
