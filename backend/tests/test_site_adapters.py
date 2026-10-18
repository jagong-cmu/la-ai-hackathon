"""Real-site adapters (WebstaurantStore, Shopify) on saved pages: no network.

tests/sites/webstaurant/ holds pages saved from the live site on 2026-10-17 with scripts,
styles and images removed. Each page is served at its real URL through page.route(), so links
and page.url look the same as on the site. A live run is app/lanes/live/adapter_gate.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.contracts import SupplierRef
from tests.test_lanes_live import needs_browser

SITES = Path(__file__).parent / "sites"
WSS = "https://www.webstaurantstore.com"
GLOVES = f"{WSS}/glove-nitrile-pfree-lg-1m-3-5mil-blk-10bx-cs/394EN403LCASE.html"


# ---------------------------------------------------------------- pure helpers


def test_webstaurant_search_slug_matches_the_site():
    from app.lanes.live.webstaurant import search_slug

    assert search_slug("nitrile gloves") == "nitrile-gloves"
    assert search_slug("hinged to-go box") == "hinged-to--go-box"  # the URL the site itself made
    assert search_slug('9" x 9" Hinged Box!') == "9-x-9-hinged-box"


def test_webstaurant_item_number_is_the_cart_sku():
    from app.lanes.live.webstaurant import item_number

    assert item_number(GLOVES) == "394EN403LCASE"
    assert item_number(f"{WSS}/cart/") is None


VARIANTS = [
    {"id": 1, "title": "Small", "sku": "S", "price": 5360, "available": True},
    {"id": 2, "title": "Medium", "sku": "M", "price": 4959, "available": True},
    {"id": 3, "title": "Large", "sku": "", "price": 4959, "available": False},
]


def test_shopify_variant_follows_the_query_and_stock():
    from app.lanes.live.shopify import _sku, pick_variant

    assert pick_variant(VARIANTS, "nitrile gloves medium", None)["id"] == 2
    assert pick_variant(VARIANTS, "nitrile gloves large", None)["id"] == 1  # Large is sold out
    assert pick_variant(VARIANTS, "nitrile gloves", None)["id"] == 1
    assert pick_variant(VARIANTS, "medium", 3)["id"] == 3  # the variant in the URL wins
    assert _sku(VARIANTS[2]) == "3"  # no SKU: the variant id, the same value /cart.js reports


def test_shopify_url_parts():
    from app.lanes.live.adapter_base import AdapterStepError
    from app.lanes.live.shopify import _handle, _variant_param

    url = "https://wees-express.com/products/sirix-black-nitrile-gloves?variant=39822624587863"
    assert _handle(url) == "sirix-black-nitrile-gloves"
    assert _variant_param(url) == 39822624587863
    assert _variant_param("https://wees-express.com/products/x") is None
    with pytest.raises(AdapterStepError):
        _handle("https://wees-express.com/cart")


# ---------------------------------------------------------------- WebstaurantStore saved pages


@pytest.fixture
async def saved_page() -> AsyncIterator:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()

        async def open_saved(url: str, name: str):
            body = (SITES / "webstaurant" / name).read_text()
            await page.route(url, lambda route: route.fulfill(body=body, content_type="text/html"))
            await page.goto(url)
            return page

        yield open_saved
        await browser.close()


def wss_lane():
    from app.lanes.live.webstaurant import WebstaurantLane

    return WebstaurantLane(SupplierRef(id="webstaurant", name="WebstaurantStore", url=f"{WSS}/",
                                       adapter="webstaurant"))


@needs_browser
async def test_webstaurant_search_results(saved_page):
    page = await saved_page(f"{WSS}/search/nitrile-gloves.html", "search.html")
    cards = await wss_lane().result_extract(page)

    assert len(cards) == 12
    first = cards[0]
    assert "Nitrile" in first.title and "Gloves" in first.title
    assert first.url.startswith(WSS) and first.url.endswith(".html")
    assert "$" in first.snippet and "ITEM NUMBER" in first.snippet.upper()


@needs_browser
async def test_webstaurant_product_page(saved_page):
    page = await saved_page(GLOVES, "product.html")
    listing = await wss_lane().product_extract(page)

    assert listing.sku == "394EN403LCASE"
    assert listing.price == 48.49
    assert "1,000/Case" in listing.title
    assert "Quantity Discounts" in listing.text  # J5 sees the tier prices too


@needs_browser
async def test_webstaurant_cart_lines_and_freight(saved_page):
    from app.lanes.live.webstaurant import SUMMARY_JS
    from app.stages.extract import money_candidates

    page = await saved_page(f"{WSS}/cart/", "cart.html")
    rows = await wss_lane().cart_rows(page)
    assert [(r.sku, r.pack_quantity, r.unit_price) for r in rows] == [("394EN403LCASE", 2, 43.99)]  # tier price

    summary = await page.evaluate(SUMMARY_JS)
    assert summary.splitlines()[0] == "Cart Subtotal $87.98"
    assert "Shipping to 49007" in summary
    assert "Ground" in summary and "Next Day" not in summary  # only the selected freight option
    values = [c.value for c in money_candidates(summary)]
    assert values[0] == 87.98 and len(values) == 2


@needs_browser
async def test_webstaurant_checkout_fields_exist(saved_page):
    from app.lanes.live import webstaurant as w

    page = await saved_page(f"{WSS}/shipping-billinginfo.cfm", "checkout.html")
    selectors = [
        w.GUEST_EMAIL, "input[name='shippingAddress.name']", "input[name='shippingAddress.companyName']",
        "input[name='shippingAddress.city']", "input[name='shippingAddress.zipCode']",
        "input[name='shippingAddress.phone']", "input[autocomplete='shipping address-line1']",
        "[data-testid=company-type-select] input[role=combobox]", "input[autocomplete='shipping address-level1']",
        w.CONTINUE,
    ]
    for selector in selectors:
        assert await page.locator(selector).count() == 1, selector


async def test_webstaurant_refuses_placeholder_contact():
    from app.config import CheckoutContact
    from app.lanes.live.adapter_base import AdapterStepError

    with pytest.raises(AdapterStepError, match="contact is not set"):
        await wss_lane().fill_contact(None, CheckoutContact())  # never reaches the page
