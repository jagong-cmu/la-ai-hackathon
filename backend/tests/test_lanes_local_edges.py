"""Edge cases for the example_site adapter on the local test site (soak backlog). No network."""

from __future__ import annotations

import contextlib
import functools
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app.contracts import CartLine, ShoppingItem, SupplierRef
from app.lanes.base import LaneGuardStopped
from tests.test_lanes_live import make_ctx, needs_browser

pytestmark = needs_browser


def _cfg():
    from app.lanes.live.config import LiveLaneSettings

    return LiveLaneSettings(_env_file=None, browser_mode="local", browser_headless=True, openrouter_api_key=None)


@contextlib.contextmanager
def _serve(root: Path):
    from app.lanes.live.testsite import _QuietHandler

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


async def test_search_for_an_item_the_site_does_not_sell(settings, decider, tmp_path):
    from app.lanes.live.example_site import ExampleSiteLane
    from app.lanes.live.testsite import serve_site

    items = [ShoppingItem(id="gloves", name="nitrile gloves medium", quantity=100, unit="gloves"),
             ShoppingItem(id="anvil", name="cast iron blacksmith anvil 50 kg", quantity=1, unit="each")]
    with serve_site() as base:
        supplier = SupplierRef(id="example", name="Demo Supply Co.", url=base, adapter="example_site")
        ctx, _ = make_ctx(settings, decider, tmp_path, supplier)
        lane = ExampleSiteLane(supplier, _cfg())
        try:
            await lane.open(ctx)
            offers = await lane.search(ctx, items)
            assert offers.status in ("ok", "partial"), offers
            assert {p.item_id for p in offers.products} == {"gloves"}  # no anvil is invented
        finally:
            await lane.close(ctx)


async def test_cart_line_with_an_unknown_sku_is_not_verified(settings, decider, tmp_path):
    from app.lanes.live.example_site import ExampleSiteLane
    from app.lanes.live.testsite import serve_site

    with serve_site() as base:
        supplier = SupplierRef(id="example", name="Demo Supply Co.", url=base, adapter="example_site")
        ctx, _ = make_ctx(settings, decider, tmp_path, supplier)
        lane = ExampleSiteLane(supplier, _cfg())
        try:
            await lane.open(ctx)
            lines = [CartLine(item_id="ghost", sku="NO-SUCH-SKU", pack_quantity=2, unit_price=5.0,
                              url=base + "product.html?sku=NO-SUCH-SKU")]
            try:
                cart = await lane.set_cart(ctx, lines)
            except Exception as exc:  # noqa: BLE001 - a clean lane error is fine; a wrong "verified" is not
                assert not isinstance(exc, AssertionError)
                return
            assert cart.verification_status != "verified", cart
        finally:
            await lane.close(ctx)


async def test_guard_stops_on_card_fields_with_unusual_names(settings, decider, tmp_path):
    """S-008 in a real browser: card inputs named cc_number / exp_month / cvv2, no autocomplete, plain labels."""
    from app.lanes.live.example_site import ExampleSiteLane
    from app.lanes.live.testsite import SITES_DIR

    root = tmp_path / "site"
    shutil.copytree(SITES_DIR / "example_site", root)
    (root / "payment.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Step 3</title>'
        '<script src="site.js"></script></head><body data-page="payment"><nav id="hdr"></nav><main id="main">'
        '<form id="pay"><label for="a">Number</label> <input id="a" name="cc_number"><br>'
        '<label for="b">Month</label> <input id="b" name="exp_month"><br>'
        '<label for="c">Code</label> <input id="c" name="cvv2"><br>'
        '<button id="place">Continue</button> <span id="msg"></span></form></main></body></html>')
    with _serve(root) as base:
        supplier = SupplierRef(id="example", name="Demo Supply Co.", url=base, adapter="example_site")
        ctx, events = make_ctx(settings, decider, tmp_path, supplier)
        lane = ExampleSiteLane(supplier, _cfg())
        try:
            await lane.open(ctx)
            with pytest.raises(LaneGuardStopped):
                await lane.goto(base + "payment.html")
            stops = [d for k, d in events if k == "guard_stop"]
            assert stops and stops[-1]["reason"] == "dom_card_field", stops
        finally:
            await lane.close(ctx)
