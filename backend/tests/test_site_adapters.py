"""Real-site adapters (WebstaurantStore, Shopify) on saved pages: no network.

tests/sites/webstaurant/ holds pages saved from the live site on 2026-10-17 with scripts,
styles and images removed. Each page is served at its real URL through page.route(), so links
and page.url look the same as on the site. A live run is app/lanes/live/adapter_gate.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="the site adapters need the browser extra")

from app.contracts import SupplierRef  # noqa: E402
from tests.test_lanes_live import needs_browser  # noqa: E402

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


def test_pair_labels_joins_each_amount_to_its_label():
    from app.lanes.live.adapter_base import pair_labels

    raw = "Subtotal · 2 items\n$119.02\nShipping\n$26.85\nTotal\nUSD\n$145.87"
    assert pair_labels(raw) == "Subtotal · 2 items $119.02\nShipping $26.85\nTotal USD $145.87"


SHOPIFY_CHECKOUT = """<form>
  <input name="email" autocomplete="shipping email">
  <input name="firstName" autocomplete="shipping given-name"><input name="lastName" autocomplete="shipping family-name">
  <input name="address1" autocomplete="shipping address-line1"><input name="city" autocomplete="shipping address-level2">
  <select name="zone"><option>Michigan</option><option>California</option></select>
  <input name="postalCode" autocomplete="{postal}">
  <button id="pay" onclick="document.body.dataset.paid = 1">Pay now</button>
</form>
<div role="table">Item Value Subtotal <span>$49.59</span>
  <div>Shipping</div><div>$26.85</div><div>Total</div><div>USD</div><div>$76.44</div></div>"""


@needs_browser
@pytest.mark.parametrize("postal, ok", [("shipping postal-code", True), ("cc-number", False)])
async def test_shopify_payment_page_fill_types_only_shipping_fields(postal, ok):
    from playwright.async_api import async_playwright

    from app.config import CheckoutContact
    from app.lanes.live.adapter_base import AdapterStepError, CardFieldRefused
    from app.lanes.live.shopify import WEES_EXPRESS, ShopifyAdapterLane

    lane = ShopifyAdapterLane(SupplierRef(id="w", name="W", url="https://wees-express.com/"), WEES_EXPRESS)
    contact = CheckoutContact(address1="325 Westwood Plaza", city="Los Angeles", state="CA", zip="90095")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(SHOPIFY_CHECKOUT.format(postal=postal))
        if ok:
            await lane.fill_contact_on_payment_page(page, contact)
            assert await page.input_value("input[name=postalCode]") == "90095"
            assert await page.eval_on_selector("select[name=zone]", "s => s.value") == "California"
            assert "Shipping $26.85" in await lane.checkout_summary(page)
        else:
            with pytest.raises((AdapterStepError, CardFieldRefused)):
                await lane.fill_contact_on_payment_page(page, contact)
            assert await page.input_value("input[name=postalCode]") == ""  # nothing typed into it
        assert await page.evaluate("document.body.dataset.paid") is None  # never clicked
        await browser.close()


def test_stub_j6_treats_the_review_and_pay_page_as_payment():
    """WebstaurantStore step 2 shows no card field until a payment method is picked, so the
    guard's DOM check cannot stop there; the stub J6 must (step 1's progress bar names step 2)."""
    import re

    from app.stub_rules import PAGE_RULES

    payment = dict(PAGE_RULES)["payment_step"]
    step1 = re.sub(r"<[^>]+>", " ", (SITES / "webstaurant" / "checkout.html").read_text())
    step2 = ("Shipping & Billing step 1 of 3 Review & Payment step 2 of 3 Order Confirmation step 3 of 3\n"
             "Review & Payment\nINFORMATION\nBilling Address\nEdit Billing Address\nShipping Address\n"
             "Change Shipping Address\nCART\nEdit")
    assert not payment.search(step1)
    assert payment.search(step2)


def test_shipping_shown_ignores_an_empty_shipping_row():
    from app.lanes.live.shopify import shipping_shown

    # While rates load, the empty Shipping label joins the next row: not a shipping amount.
    assert not shipping_shown("Subtotal $223.82\nShipping Estimated taxes The final tax and total will be "
                              "confirmed by email or text after you place your order. $22.94\nTotal USD $246.76")
    assert not shipping_shown("Subtotal $99.18\nShipping Total USD $119.02")
    assert shipping_shown("Subtotal $223.82\nShipping $18.50\nEstimated taxes $24.60\nTotal USD $266.92")



def test_tax_rows_for_j7():
    from app.lanes.live.shopify import clean_tax_rows, tax_shown

    # Live Restaurantware summaries, 2026-10-18.
    loading = "Subtotal $223.82\nShipping $22.72\nTaxes Total USD $246.54"
    assert not tax_shown(loading)
    assert clean_tax_rows(loading) == "Subtotal $223.82\nShipping $22.72\nTotal USD $246.54"
    sentence = ("Subtotal $223.82\nShipping $22.72\nEstimated taxes The final tax and total will be confirmed by "
                "email or text after you place your order. $22.94\nTotal USD $269.48")
    assert tax_shown(sentence)
    assert clean_tax_rows(sentence) == ("Subtotal $223.82\nShipping $22.72\nEstimated taxes $22.94\n"
                                        "Total USD $269.48")
    plain = "Subtotal $99.18\nShipping $8.00\nTaxes $0.00\nTotal USD $107.18"
    assert tax_shown(plain) and clean_tax_rows(plain) == plain


@pytest.fixture
def broken_fetch_store() -> AsyncIterator:
    """A local Shopify-like store whose page fetch() always fails, like Wees Express in the cloud
    browser on 2026-10-18. The JSON URLs and the /cart/add and /cart/change GET forms answer, and
    the cart lives behind a cart cookie, as on Shopify."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    product = {"title": "Bakery Box 10 x 10 x 5", "description": "<p>White. 100 per case.</p>",
               "variants": [{"id": 7, "title": "Default Title", "sku": "BB-10", "price": 4599, "available": True}]}
    pages = {
        "/cart": ("text/html", "<html><script>window.fetch = () => Promise.reject("
                               "new TypeError('Failed to fetch'))</script><body>Cart</body></html>"),
        # A fetch that never settles until its signal aborts (job_458035c3a1 waited 45 s on one).
        "/cart-hangs": ("text/html", "<html><script>window.fetch = (u, o) => new Promise((_, reject) =>"
                                     " o?.signal?.addEventListener('abort', () => reject(o.signal.reason)))"
                                     "</script><body>Cart</body></html>"),
        "/search/suggest.json": ("application/json", json.dumps(
            {"resources": {"results": {"products": [{"title": product["title"], "handle": "bakery-box",
                                                     "price": "45.99"}]}}})),
        "/products/bakery-box.js": ("application/json", json.dumps(product)),
    }
    carts: dict[str, dict[int, int]] = {}  # cart cookie -> variant -> quantity
    cart_cookies: list[str] = []  # the Cookie header of each GET /cart

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            token = (self.headers.get("Cookie") or "").partition("cart=")[2].split(";")[0]
            new_token = None
            if url.path in ("/cart/add", "/cart/change"):
                if not token:
                    token = new_token = f"tok{len(carts) + 1}"
                cart = carts.setdefault(token, {})
                variant, quantity = int(query["id"]), int(query["quantity"])
                cart[variant] = cart.get(variant, 0) + quantity if url.path == "/cart/add" else quantity
                if not cart[variant]:
                    del cart[variant]
                url = url._replace(path="/cart")
            if url.path == "/cart":
                cart_cookies.append(self.headers.get("Cookie") or "")
            if url.path == "/cart.js":
                items = [{"sku": "BB-10", "variant_id": v, "quantity": q, "final_price": 4599}
                         for v, q in carts.get(token, {}).items()]
                pages["/cart.js"] = ("application/json", json.dumps({"items": items}))
            kind, body = pages.get(url.path, ("text/plain", ""))
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", kind)
            if new_token:
                self.send_header("Set-Cookie", f"cart={new_token}; Path=/")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):  # /cart/add.js, for a client that can post outside the page
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            token = (self.headers.get("Cookie") or "").partition("cart=")[2].split(";")[0]
            new_token = None if token else f"tok{len(carts) + 1}"
            cart = carts.setdefault(token or new_token, {})
            for item in body.get("items", []):
                cart[item["id"]] = cart.get(item["id"], 0) + item["quantity"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if new_token:
                self.send_header("Set-Cookie", f"cart={new_token}; Path=/")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.cart_cookies = cart_cookies
    yield server
    server.shutdown()


@pytest.fixture
async def cdp_browser() -> AsyncIterator:
    """Chromium attached over CDP, as the cloud browser is. A launched browser can hide CDP-only
    behavior: on 2026-10-18, context.request sent no cookie that the page had set on a CDP browser."""
    import socket
    import tempfile

    from playwright.async_api import async_playwright

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    async with async_playwright() as pw:
        proc = await asyncio.create_subprocess_exec(
            pw.chromium.executable_path, f"--remote-debugging-port={port}", "--headless=new",
            f"--user-data-dir={tempfile.mkdtemp()}", "about:blank", stderr=asyncio.subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception:
                    await asyncio.sleep(0.1)
            yield browser
            await browser.close()
        finally:
            proc.terminate()
            await proc.wait()


@needs_browser
async def test_shopify_works_when_the_page_fetch_fails(broken_fetch_store, cdp_browser):
    """Live 2026-10-18 on Wees Express: the search (job_993d36dce8), then the cart (job_8ec7d51952)
    failed with 'Failed to fetch'. With a context.request fallback, the cart then read $0.00
    (job_4fdc7ee9ff). The fallback is now the browser's own page loads in another tab."""
    from app.contracts import CartLine
    from app.lanes.live.shopify import WEES_EXPRESS, ShopifyAdapterLane

    store = f"http://127.0.0.1:{broken_fetch_store.server_port}"
    lane = ShopifyAdapterLane(SupplierRef(id="w", name="W", url=f"{store}/"), WEES_EXPRESS)

    async def no_guard(page, **kwargs):
        return None

    lane.after_nav = no_guard  # the payment guard has its own tests
    context = cdp_browser.contexts[0]
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(f"{store}/cart")
    cards = await lane.search_cards(page, "white bakery box")
    assert [c.url for c in cards] == [f"{store}/products/bakery-box"]
    listing = await lane.card_listing(page, cards[0])
    assert listing is not None and listing.sku == "BB-10" and listing.price == 45.99

    line = CartLine(item_id="boxes", sku="BB-10", pack_quantity=5, unit_price=45.99, url=listing.url)
    assert await lane.add_lines(page, [line])
    assert [(r.sku, r.pack_quantity) for r in await lane.cart_rows(page)] == [("BB-10", 5)]
    await page.goto(f"{store}/cart")  # the browser's own cart page has the same cart
    assert "cart=tok1" in broken_fetch_store.cart_cookies[-1]
    await lane.remove_item(page, "BB-10")
    assert await lane.cart_rows(page) == []
    assert len(context.pages) == 1  # the extra tabs are closed


@needs_browser
async def test_shopify_falls_back_when_the_page_fetch_hangs(broken_fetch_store, cdp_browser):
    import time

    from app.lanes.live.shopify import WEES_EXPRESS, ShopifyAdapterLane

    store = f"http://127.0.0.1:{broken_fetch_store.server_port}"
    lane = ShopifyAdapterLane(SupplierRef(id="w", name="W", url=f"{store}/"), WEES_EXPRESS)
    lane.fetch_timeout_ms = 200
    context = cdp_browser.contexts[0]
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(f"{store}/cart-hangs")
    started = time.monotonic()
    cards = await asyncio.wait_for(lane.search_cards(page, "white bakery box"), 5)  # unbounded before
    assert [c.url for c in cards] == [f"{store}/products/bakery-box"]
    assert time.monotonic() - started < 3
