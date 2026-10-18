"""Live lane tests on a local static site (backend/tests/sites/example_site). No real supplier sites.

The adapter test needs the `browser` extra and Playwright's Chromium:
    uv sync --extra browser && uv run playwright install chromium
It skips cleanly without them. Jev runs in stub mode (no keys).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from app.contracts import CartLine, PageInput, PageSnapshot, ShoppingItem, SupplierRef
from app.lanes.base import LaneContext, LaneGuardStopped

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def _chromium_ok() -> bool:
    if not HAS_PLAYWRIGHT:
        return False
    import asyncio

    from playwright.async_api import async_playwright

    async def probe() -> str:
        async with async_playwright() as p:
            return p.chromium.executable_path

    try:
        return Path(asyncio.run(probe())).exists()
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _chromium_ok(), reason="playwright/chromium not installed")


def make_ctx(settings, decider, tmp_path: Path, supplier: SupplierRef) -> tuple[LaneContext, list]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(kind: str, data: dict[str, Any]) -> None:
        events.append((kind, data))

    ctx = LaneContext(job_id="job_live", lane_id="lane_1", supplier=supplier, settings=settings, decider=decider,
                      emit=emit, evidence_dir=tmp_path / "evidence")
    return ctx, events


PAYMENT_PAGE = PageSnapshot(
    url="http://127.0.0.1/payment.html",
    visible_text="Payment. Place order.",
    inputs=[PageInput(name="cardnumber", id="cc-number", autocomplete="cc-number", label="Card number")],
)
CART_PAGE = PageSnapshot(url="http://127.0.0.1/cart.html", visible_text="Your cart\nSubtotal $10.00")


# ---------------------------------------------------------------- unit: the guard aborts


async def test_require_safe_raises_on_card_field(settings, decider, tmp_path):
    from app.lanes.live import guards

    ctx, events = make_ctx(settings, decider, tmp_path, SupplierRef(id="s", name="S", url="http://127.0.0.1/"))
    with pytest.raises(LaneGuardStopped) as err:
        await guards.require_safe(ctx, PAYMENT_PAGE)
    assert err.value.verdict.reason == "dom_card_field"
    assert ("guard_stop", {"reason": "dom_card_field", "url": PAYMENT_PAGE.url}) in events
    assert (await guards.require_safe(ctx, CART_PAGE)).stop is False


async def test_agent_tools_stop_on_card_field(settings, decider, tmp_path):
    """The agent's page_state tool and the before-action hook both stop on a card field."""
    from app.lanes.live.tools import ToolCore

    ctx, events = make_ctx(settings, decider, tmp_path, SupplierRef(id="s", name="S", url="http://127.0.0.1/"))
    current = {"page": CART_PAGE}

    async def snapshot() -> PageSnapshot:
        return current["page"]

    core = ToolCore(ctx, snapshot)
    assert await core.check_dom() is False
    message, stop = await core.page_state()
    assert stop is False and core.stop_verdict is None
    current["page"] = PAYMENT_PAGE
    assert await core.check_dom() is True  # register_should_stop_callback -> the agent stops before acting
    core.stop_verdict = None
    message, stop = await core.page_state()
    assert stop and message.startswith("STOP") and core.stop_verdict.reason == "dom_card_field"
    assert any(k == "guard_stop" for k, _ in events)


@pytest.mark.skipif(importlib.util.find_spec("browser_use") is None, reason="browser-use not installed")
def test_browser_use_tools_register(settings, decider, tmp_path):
    from app.lanes.live.agent_lane import LaneAgentOutput
    from app.lanes.live.tools import EXCLUDED_ACTIONS, ToolCore, build_tools

    ctx, _ = make_ctx(settings, decider, tmp_path, SupplierRef(id="s", name="S", url="http://127.0.0.1/"))

    async def snapshot() -> PageSnapshot:
        return CART_PAGE

    tools = build_tools(ToolCore(ctx, snapshot), LaneAgentOutput)
    names = set(tools.registry.registry.actions)
    assert {"page_state", "rank_results", "judge_listing", "read_cart_totals", "done"} <= names
    assert not names & set(EXCLUDED_ACTIONS)


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")
async def test_agent_lane_judges_pages_the_agent_skipped(settings, decider, tmp_path):
    """H3 gate: the agent named a product page but did not call judge_listing. One repair run judges it."""
    from types import SimpleNamespace

    from app.contracts import Product
    from app.lanes.live.agent_lane import AgentFound, BrowserUseAgentLane, LaneAgentOutput
    from app.lanes.live.config import LiveLaneSettings
    from app.lanes.live.tools import ToolCore

    supplier = SupplierRef(id="s", name="S", url="http://127.0.0.1/")
    ctx, _ = make_ctx(settings, decider, tmp_path, supplier)
    lane = BrowserUseAgentLane(supplier, LiveLaneSettings(_env_file=None))

    async def snapshot() -> PageSnapshot:
        return CART_PAGE

    lane.core = ToolCore(ctx, snapshot)
    tasks: list[str] = []
    url = "http://127.0.0.1/product-gloves.html"

    async def fake_run(ctx: Any, task: str, tag: str) -> Any:
        tasks.append(tag)
        if tag == "judge":  # the repair run calls judge_listing, which records the product
            assert url in task
            lane.core.products["gloves"] = [Product(item_id="gloves", title="Gloves", sku="G1", url=url,
                                                    pack_price=8.49)]
        found = [AgentFound(item_id="gloves", product_url=url), AgentFound(item_id="other", product_url=url)]
        return SimpleNamespace(structured_output=LaneAgentOutput(found=found))

    lane._run = fake_run  # type: ignore[method-assign]
    items = [ShoppingItem(id="gloves", name="nitrile gloves medium", quantity=300, unit="gloves")]
    offers = await lane.search(ctx, items)
    assert tasks == ["search", "judge"]
    assert offers.status == "ok" and offers.products[0].sku == "G1"


def test_live_setup_never_breaks_import():
    from app.lanes import live_setup

    assert set(live_setup.LIVE_LANES) == {"example_site", "webstaurant", "wees_express", "restaurantware", "llm_agent",
                                       "hosted_agent"}


# ---------------------------------------------------------------- end to end: example adapter


@needs_browser
async def test_example_adapter_end_to_end(settings, decider, tmp_path):
    from app.config import CheckoutContact
    from app.lanes.live.config import LiveLaneSettings
    from app.lanes.live.example_site import ExampleSiteLane
    from app.lanes.live.testsite import serve_site

    cfg = LiveLaneSettings(_env_file=None, browser_mode="local", browser_headless=True, openrouter_api_key=None)
    items = [
        ShoppingItem(id="gloves", name="nitrile gloves medium", quantity=300, unit="gloves"),
        ShoppingItem(id="boxes", name="white bakery box 10 x 10 x 5", quantity=50, unit="boxes"),
    ]
    with serve_site() as base:
        supplier = SupplierRef(id="example", name="Demo Supply Co.", url=base, adapter="example_site")
        ctx, events = make_ctx(settings, decider, tmp_path, supplier)
        lane = ExampleSiteLane(supplier, cfg)
        try:
            await lane.open(ctx)
            assert ctx.session_id and ctx.session_id.startswith("local-")
            assert ctx.live_url is None  # local headless browser: no live view

            # search -> J4 -> J5
            offers = await lane.search(ctx, items)
            assert offers.status == "ok", offers
            by_item = {p.item_id: p for p in offers.products}
            gloves, boxes = by_item["gloves"], by_item["boxes"]
            assert (gloves.sku, gloves.pack_price, gloves.units_per_pack) == ("GLV-NM-100", 8.49, 100)
            assert (boxes.sku, boxes.pack_price, boxes.units_per_pack) == ("BOX-10105-50", 32.5, 50)
            assert gloves.jev is not None and gloves.jev.match_probs["2"] >= 0.8
            assert offers.free_shipping_threshold == 75.0

            # full cart -> J7 -> code sum check (the "You saved" line is a decoy)
            lines = [
                CartLine(item_id="gloves", sku=gloves.sku, pack_quantity=3, unit_price=8.49, url=gloves.url),
                CartLine(item_id="boxes", sku=boxes.sku, pack_quantity=1, unit_price=32.5, url=boxes.url),
            ]
            cart = await lane.set_cart(ctx, lines)
            assert cart.verification_status == "verified", cart.reason
            assert cart.totals_check == "consistent"
            assert cart.costs is not None
            assert (cart.costs.subtotal, cart.costs.shipping, cart.costs.tax, cart.costs.total) == (
                57.97, 9.95, 3.48, 71.40)
            assert any("saved" in c.label for c in cart.money_candidates)
            assert cart.session_id == ctx.session_id

            # subset in the same cart: remove the boxes
            subset = await lane.set_cart(ctx, lines[:1])
            assert subset.verification_status == "verified", subset.reason
            assert subset.costs is not None and subset.costs.total == 36.95
            assert [i.sku for i in subset.cart_items] == ["GLV-NM-100"]

            # approved plan: back to the full cart, then checkout -> the guard stops at payment
            cart = await lane.set_cart(ctx, lines)
            assert cart.verification_status == "verified" and cart.costs.total == 71.40
            handoff = await lane.checkout(ctx, CheckoutContact())
            assert handoff.status == "at_payment", handoff
            assert handoff.screenshot_url and handoff.screenshot_url.startswith("/evidence/job_live/example/")

            stops = [d for k, d in events if k == "guard_stop"]
            assert stops and stops[-1]["reason"] == "dom_card_field"
            assert stops[-1]["url"].endswith("/payment.html")
            assert ("checkout_filled" in [k for k, _ in events])

            # the browser is still on the payment page, and no payment field has a value
            page = await lane.browser.page()
            assert page.url.endswith("/payment.html")
            for sel in ("#cc-number", "#cc-exp", "#cc-csc"):
                assert await page.input_value(sel) == ""
            assert await page.inner_text("#msg") == ""  # "Place order" was never clicked

            shots = list((tmp_path / "evidence" / "job_live" / "example").glob("*.jpg"))
            assert shots, "screenshots are written as evidence"
        finally:
            await lane.close(ctx)


@needs_browser
async def test_adapter_refuses_to_type_into_card_field(settings, decider, tmp_path):
    from app.lanes.live.config import LiveLaneSettings
    from app.lanes.live.example_site import ExampleSiteLane
    from app.lanes.live.testsite import serve_site

    cfg = LiveLaneSettings(_env_file=None, browser_mode="local", browser_headless=True, openrouter_api_key=None)
    with serve_site() as base:
        supplier = SupplierRef(id="example", name="Demo Supply Co.", url=base, adapter="example_site")
        ctx, _ = make_ctx(settings, decider, tmp_path, supplier)
        lane = ExampleSiteLane(supplier, cfg)
        try:
            await lane.open(ctx)
            with pytest.raises(LaneGuardStopped):
                await lane.goto(base + "payment.html")  # the guard runs after navigation
            page = await lane.browser.page()
            with pytest.raises(LaneGuardStopped):
                await lane.fill(page, "#cc-number", "0000")  # the guard runs before typing
            assert await page.input_value("#cc-number") == ""
        finally:
            await lane.close(ctx)


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")  # warm imports session
async def test_warm_pool_hands_one_browser_to_one_lane(monkeypatch):
    from app.lanes.live import warm

    started: list[str] = []

    class FakeBrowser:
        def __init__(self, cfg, label="lane"):
            self.label = label

        async def start(self):
            started.append(self.label)

        async def close(self):
            pass

    monkeypatch.setattr(warm, "LaneBrowser", FakeBrowser)
    a = SupplierRef(id="a", name="A", url="https://a.example", adapter="x")
    await warm.prewarm([a, a], cfg=object())  # the same supplier twice starts one browser
    first = await warm.claim(a)
    assert started == ["a"] and isinstance(first, FakeBrowser)
    assert await warm.claim(a) is None  # taken: the next lane starts its own
    await warm.close_all()
