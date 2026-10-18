"""Hosted-agent lane (app/lanes/live/hosted_agent.py) against a fake Browser Use v4 API.

No network and no credit: httpx.MockTransport answers every call. The stub Jev answers J5, J6
and J7, so the lane's real code paths run: domain check, J5, J7, build_verified_cart, the guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import CheckoutContact
from app.contracts import CartLine, SearchSpec, ShoppingItem, SupplierRef
from app.lanes.base import LaneBlocked, LaneContext, LaneGuardStopped
from app.lanes.live.config import LiveLaneSettings
from app.lanes.live.hosted_agent import (
    FORBIDDEN_RUN_FIELDS,
    HostedAgentClient,
    HostedAgentLane,
    HostedCartOutput,
    HostedRunError,
    output_schema,
    run_body,
    view_urls,
)

SUPPLIER = SupplierRef(id="acme", name="Acme Supply", url="https://www.acme-supply.com")
LIVE_VIEW = "https://live.browser-use.com/v/abc?token=t"
CART_PAGE = {"url": "https://www.acme-supply.com/cart", "visible_text": "Your cart. Subtotal $25.00", "inputs": []}
REVIEW_PAGE = {"url": "https://www.acme-supply.com/checkout/review",
               "visible_text": "Order summary. Shipping $5.00. Tax $2.00. Total $32.00", "inputs": []}
PAYMENT_PAGE = {"url": "https://www.acme-supply.com/checkout/pay", "visible_text": "Payment. Card number",
                "inputs": [{"name": "cardnumber", "autocomplete": "cc-number", "label": "Card number"}]}


class FakeBrowserUse:
    """Each POST /runs pops the next scripted output. Records every request body."""

    def __init__(self, outputs: list[dict[str, Any]], *, fail_status: str | None = None):
        self.outputs = list(outputs)
        self.bodies: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.fail_status = fail_status
        self.runs: dict[str, dict[str, Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v4")
        if request.method == "POST" and path == "/runs":
            body = json.loads(request.content)
            self.bodies.append(body)
            run_id = f"run-{len(self.bodies)}"
            self.runs[run_id] = self.outputs.pop(0)
            return httpx.Response(200, json={"id": run_id, "status": "queued", "model": "m",
                                             "sessionId": "sess-1", "workspaceId": "ws-1", "eventsUrl": ""})
        if path.endswith("/status"):
            return httpx.Response(200, json={"status": "completed"})
        if path.endswith("/events"):
            return httpx.Response(200, json={"events": [
                {"runId": "x", "id": 1, "ts": "t", "type": "browser.ready", "data": {"live_view_url": LIVE_VIEW}}],
                "nextAfter": 1, "hasMore": False})
        if request.method == "GET" and path.startswith("/runs/"):
            run_id = path.split("/")[2]
            status = self.fail_status or "completed"
            return httpx.Response(200, json={"id": run_id, "status": status, "output": self.runs[run_id],
                                             "error": "boom" if self.fail_status else None, "totalCostUsd": "0.10"})
        if path == "/workspaces/ws-1/files":
            return httpx.Response(200, json={"files": [{"path": "evidence/cart.png", "url": "https://files.test/c.png"}],
                                             "hasMore": False})
        if request.url.host == "files.test":
            return httpx.Response(200, content=b"\x89PNG fake")
        if request.method == "DELETE" and path.startswith("/sessions/"):
            self.deleted.append(path.split("/")[2])
            return httpx.Response(204)
        if path.endswith("/cancel"):
            return httpx.Response(200, json={})
        return httpx.Response(404, text=f"unexpected {request.method} {path}")


def cfg() -> LiveLaneSettings:
    return LiveLaneSettings(_env_file=None, browser_use_api_key="bu-test", hosted_agent_poll_s=0)


def make_lane(fake: FakeBrowserUse) -> HostedAgentLane:
    c = cfg()
    return HostedAgentLane(SUPPLIER, c, client=HostedAgentClient(c, transport=httpx.MockTransport(fake)))


def make_ctx(settings, decider, tmp_path: Path) -> tuple[LaneContext, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []

    async def emit(kind: str, data: dict[str, Any]) -> None:
        events.append((kind, data))

    return LaneContext(job_id="job_h", lane_id="lane_acme", supplier=SUPPLIER, settings=settings,
                       decider=decider, emit=emit, evidence_dir=tmp_path / "evidence"), events


OPEN = {"final_page": {"url": "https://www.acme-supply.com/", "visible_text": "Acme Supply home", "inputs": []}}
ITEMS = [ShoppingItem(id="gloves", name="nitrile gloves, large", quantity=10, unit="box")]
LINES = [CartLine(item_id="gloves", sku="G-100", pack_quantity=2, unit_price=12.5,
                  url="https://www.acme-supply.com/p/g-100")]


def cart_out(**over: Any) -> dict[str, Any]:
    out = {
        "lines": [{"line_id": "G-100", "observed_title": "Nitrile gloves L", "observed_sku": "G-100",
                   "packs": 2, "line_total_cents": 2500}],
        "costs": {"subtotal_cents": 2500, "shipping_cents": 500, "tax_cents": 200, "fees_cents": None,
                  "discounts_cents": None, "total_cents": 3200},
        "reached_complete_total": True, "order_submitted": False, "final_page": REVIEW_PAGE,
    }
    out.update(over)
    return out


# ---------------------------------------------------------------- the run body


def test_run_body_never_sends_a_way_to_pay():
    body = run_body(cfg(), "task", {}, None)
    assert not FORBIDDEN_RUN_FIELDS & body.keys()
    assert body["maxCostUsd"] == 2.0 and body["agentmail"] is False and "sessionId" not in body
    follow_up = run_body(cfg(), "task", {}, "sess-1")
    assert follow_up["sessionId"] == "sess-1" and "maxCostUsd" not in follow_up


def test_run_body_asks_for_low_reasoning_on_the_default_model():
    assert run_body(cfg(), "task", {}, None)["modelParams"] == {"reasoning": {"effort": "low"}}
    other = LiveLaneSettings(_env_file=None, browser_use_api_key="bu-test", hosted_agent_model="gemini-3.6-flash")
    body = run_body(other, "task", {}, None)
    assert body["model"] == "gemini-3.6-flash" and "modelParams" not in body


def test_cart_prompt_has_no_screenshot_step_by_default():
    from app.lanes.live.hosted_agent import cart_prompt, search_prompt

    assert "screenshot" not in cart_prompt(SUPPLIER, LINES, None).lower()
    assert "screenshot" in cart_prompt(SUPPLIER, LINES, None, screenshot=True).lower()
    spec = {"gloves": SearchSpec(item_id="gloves", query="nitrile gloves", unit="box")}
    assert "https://www.acme-supply.com/search?q=" in search_prompt(SUPPLIER, ITEMS, spec)


async def test_different_listings_are_dropped_and_differences_reach_j5(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, {
        "listings": [
            {"item_id": "gloves", "title": "Vinyl gloves, large, 100 per box", "sku": "V-1",
             "product_url": "https://www.acme-supply.com/p/v-1", "pack_price_cents": 900, "in_stock": True,
             "listing_text": "Box of 100 vinyl gloves.", "match_status": "different",
             "differences": ["vinyl, not nitrile"]},
            {"item_id": "gloves", "title": "Nitrile gloves, large, 100 per box", "sku": "G-100",
             "product_url": "https://www.acme-supply.com/p/g-100", "pack_price_cents": 1250, "in_stock": True,
             "listing_text": "Box of 100 gloves.", "match_status": "uncertain",
             "differences": ["color not shown"]},
        ],
        "final_page": CART_PAGE,
    }])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    offers = await lane.search(ctx, ITEMS)
    assert [p.sku for p in offers.products] == ["G-100"]
    assert "rejected Vinyl gloves" in (offers.error or "")
    saved = json.loads((tmp_path / "evidence" / "job_h" / "acme" / "listing_gloves_G-100.json").read_text())
    assert saved["matchStatus"] == "uncertain" and saved["differences"] == ["color not shown"]


async def test_no_matching_listing_is_not_found(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, {"listings": [], "missing_item_ids": ["gloves"], "final_page": CART_PAGE}])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    offers = await lane.search(ctx, ITEMS)
    assert offers.status == "not_found" and offers.products == []


def test_output_schema_has_no_refs():
    assert "$ref" not in json.dumps(output_schema(HostedCartOutput))


def test_live_view_urls_must_be_browser_use():
    read_only, interactive = view_urls(LIVE_VIEW)
    assert "readOnly=true" in read_only and interactive == LIVE_VIEW
    assert view_urls("https://evil.example/v/abc") is None
    assert view_urls("http://live.browser-use.com/v/abc") is None


# ---------------------------------------------------------------- lane steps


async def test_open_and_search_judge_listings_in_code(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, {
        "listings": [
            {"item_id": "gloves", "title": "Nitrile gloves, large, 100 per box", "sku": "G-100",
             "product_url": "https://www.acme-supply.com/p/g-100", "pack_price_cents": 1250, "in_stock": True,
             "listing_text": "Box of 100 gloves. Size large.", "match_status": "exact"},
            {"item_id": "gloves", "title": "Gloves elsewhere", "sku": "X",
             "product_url": "https://other-shop.com/p/x", "pack_price_cents": 1, "in_stock": True,
             "listing_text": "1 box", "match_status": "exact"},
        ],
        "missing_item_ids": [], "errors": [], "final_page": CART_PAGE,
    }])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    assert ctx.session_id == "sess-1" and ctx.live_url and "readOnly=true" in ctx.live_url
    offers = await lane.search(ctx, ITEMS)
    assert offers.status == "ok" and len(offers.products) == 1
    product = offers.products[0]
    assert (product.sku, product.pack_price, product.units_per_pack) == ("G-100", 12.5, 100)
    assert product.jev is not None
    assert "off-site product URL" in (offers.error or "")
    assert fake.bodies[1]["sessionId"] == "sess-1"  # the search reuses the open run's browser


async def test_cart_with_full_totals_is_verified(settings, decider, tmp_path):
    settings.checkout_contact = CheckoutContact(address1="1 Team Way")
    fake = FakeBrowserUse([OPEN, cart_out()])
    lane = make_lane(fake)
    ctx, events = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    cart = await lane.set_cart(ctx, LINES)
    assert cart.verification_status == "verified", cart.reason
    assert cart.costs is not None and (cart.costs.subtotal, cart.costs.total) == (25.0, 32.0)
    assert cart.screenshot_url is None and "screenshot" not in fake.bodies[1]["task"].lower()  # off by default
    assert "1 Team Way" in fake.bodies[1]["task"]


async def test_cart_screenshot_when_turned_on(settings, decider, tmp_path):
    settings.checkout_contact = CheckoutContact(address1="1 Team Way")
    fake = FakeBrowserUse([OPEN, cart_out()])
    c = LiveLaneSettings(_env_file=None, browser_use_api_key="bu-test", hosted_agent_poll_s=0,
                         hosted_agent_screenshot=True)
    lane = HostedAgentLane(SUPPLIER, c, client=HostedAgentClient(c, transport=httpx.MockTransport(fake)))
    ctx, events = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    cart = await lane.set_cart(ctx, LINES)
    assert cart.screenshot_url and ("screenshot", {"url": cart.screenshot_url}) in events


async def test_cart_without_team_address_stays_on_cart_and_is_estimated(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, cart_out(
        costs={"subtotal_cents": 2500, "total_cents": None}, reached_complete_total=False, final_page=CART_PAGE)])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    cart = await lane.set_cart(ctx, LINES)
    assert cart.verification_status == "estimated"
    assert "Do not start checkout" in fake.bodies[1]["task"]


async def test_wrong_pack_count_fails_the_cart(settings, decider, tmp_path):
    settings.checkout_contact = CheckoutContact(address1="1 Team Way")
    bad = cart_out()
    bad["lines"][0]["packs"] = 3
    fake = FakeBrowserUse([OPEN, bad])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    cart = await lane.set_cart(ctx, LINES)
    assert cart.verification_status == "failed" and "cart_lines_packs:G-100" in (cart.reason or "")


async def test_reported_order_submission_stops_the_lane(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, cart_out(order_submitted=True)])
    lane = make_lane(fake)
    ctx, events = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    with pytest.raises(LaneGuardStopped):
        await lane.set_cart(ctx, LINES)
    assert any(kind == "guard_stop" for kind, _ in events)


async def test_payment_page_during_search_stops_the_lane(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, {"listings": [], "final_page": PAYMENT_PAGE}])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    with pytest.raises(LaneGuardStopped):
        await lane.search(ctx, ITEMS)


async def test_blocked_site_raises_lane_blocked(settings, decider, tmp_path):
    fake = FakeBrowserUse([{**OPEN, "blocker": "bot_check"}])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    with pytest.raises(LaneBlocked):
        await lane.open(ctx)


async def test_checkout_hands_off_at_payment_with_interactive_view(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, {"reached_payment_step": True, "final_page": PAYMENT_PAGE}])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    handoff = await lane.checkout(ctx, CheckoutContact(address1="1 Team Way"))
    assert handoff.status == "at_payment" and handoff.live_url == LIVE_VIEW


async def test_checkout_that_never_reaches_payment_fails(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, {"reached_payment_step": False, "final_page": REVIEW_PAGE, "error": "stuck"}])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    handoff = await lane.checkout(ctx, CheckoutContact(address1="1 Team Way"))
    assert handoff.status == "failed" and handoff.reason == "stuck"


async def test_failed_search_run_without_fallback_reports_failed_offers(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN, {}])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    fake.fail_status = "failed"
    offers = await lane.search(ctx, ITEMS)
    assert offers.status == "failed" and "hosted_agent" in (offers.error or "")


async def test_close_deletes_the_session(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    await lane.close(ctx)
    assert fake.deleted == ["sess-1"]


def test_hosted_agent_is_the_lane_for_unknown_sites(monkeypatch):
    from app.lanes import live_setup, registry
    from app.lanes.live import config, hosted_agent

    c = cfg()
    monkeypatch.setattr(config, "get_live_settings", lambda: c)
    monkeypatch.setattr(hosted_agent, "get_live_settings", lambda: c)
    done = live_setup.register_live_lanes()
    assert done["hosted_agent"] and not done["llm_agent"]
    assert registry._LLM_AGENT is not None
    assert isinstance(registry._LLM_AGENT(SUPPLIER), HostedAgentLane)


async def test_a_short_api_outage_is_retried(settings, decider, tmp_path):
    fake = FakeBrowserUse([OPEN])
    flaky = {"left": 2}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status") and flaky["left"] > 0:
            flaky["left"] -= 1
            return httpx.Response(503, text="overloaded")
        return fake(request)

    c = cfg()
    client = HostedAgentClient(c, transport=httpx.MockTransport(handler))
    client.retry_delay_s = 0
    lane = HostedAgentLane(SUPPLIER, c, client=client)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    assert flaky["left"] == 0 and ctx.session_id == "sess-1"


async def test_a_client_error_is_not_retried(settings, decider, tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    c = cfg()
    client = HostedAgentClient(c, transport=httpx.MockTransport(handler))
    client.retry_delay_s = 0
    lane = HostedAgentLane(SUPPLIER, c, client=client)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    with pytest.raises(HostedRunError):
        await lane.open(ctx)
    assert calls["n"] == 1


def test_the_lane_sets_its_own_step_budget(settings, db, decider):
    from app.contracts import LaneStatus
    from app.orchestrator import LaneRuntime

    lane = make_lane(FakeBrowserUse([]))
    lr = LaneRuntime(status=LaneStatus(lane_id="lane_acme", supplier_id="acme", mode="llm_agent"), lane=lane)
    from app.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.settings = settings
    assert Orchestrator._budget(orch, lr, 45) == 45 * 8
    lr.lane = None
    assert Orchestrator._budget(orch, lr, 45) == 45 * settings.lane_agent_budget_factor


async def test_a_search_that_could_not_run_is_failed_not_missing(settings, decider, tmp_path):
    broken = {"listings": [], "missing_item_ids": ["gloves"],
              "errors": ["The search results did not render."], "final_page": CART_PAGE}
    fake = FakeBrowserUse([OPEN, broken, broken])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    offers = await lane.search(ctx, ITEMS)
    assert offers.status == "failed" and "did not render" in (offers.error or "")



async def test_a_failed_search_runs_once_more_with_high_reasoning(settings, decider, tmp_path):
    broken = {"listings": [], "errors": ["The search results did not render."], "final_page": CART_PAGE}
    found = {"listings": [{"item_id": "gloves", "title": "Nitrile gloves, large, 100 per box", "sku": "G-100",
                           "product_url": "https://www.acme-supply.com/p/g-100", "pack_price_cents": 1250,
                           "in_stock": True, "listing_text": "Box of 100 gloves.", "match_status": "exact"}],
             "final_page": CART_PAGE}
    fake = FakeBrowserUse([OPEN, broken, found])
    lane = make_lane(fake)
    ctx, events = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    offers = await lane.search(ctx, ITEMS)
    assert offers.status == "ok" and [p.sku for p in offers.products] == ["G-100"]
    assert fake.bodies[1]["modelParams"]["reasoning"]["effort"] == "low"
    assert fake.bodies[2]["modelParams"]["reasoning"]["effort"] == "high"
    assert any(kind == "hosted_retry" for kind, _ in events)


async def test_j5_sees_the_stock_state_that_the_agent_read(settings, decider, tmp_path, monkeypatch):
    from app.lanes.live import hosted_agent

    seen: list[str] = []
    real = hosted_agent.j5_listing

    async def spy(d, spec, title, text, job_id=None):
        seen.append(text)
        return await real(d, spec, title, text, job_id=job_id)

    monkeypatch.setattr(hosted_agent, "j5_listing", spy)
    fake = FakeBrowserUse([OPEN, {"listings": [
        {"item_id": "gloves", "title": "Nitrile gloves, large, 100 per box", "sku": "G-100",
         "product_url": "https://www.acme-supply.com/p/g-100", "pack_price_cents": 1250, "in_stock": True,
         "listing_text": "Box of 100 gloves.", "match_status": "exact"}], "final_page": CART_PAGE}])
    lane = make_lane(fake)
    ctx, _ = make_ctx(settings, decider, tmp_path)
    await lane.open(ctx)
    await lane.search(ctx, ITEMS)
    assert seen and "Availability on the page: in stock" in seen[0]
