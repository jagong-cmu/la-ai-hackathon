"""Adapter for standard Shopify stores (Wees Express and any store with the same endpoints).

Shopify stores share their data URLs whatever the theme, so this adapter reads the store's
own JSON instead of theme markup:
- `/products/<handle>.js`: title, variants (SKU, price in cents, stock), description.
- `/cart/add?id=<variant>&quantity=<n>` and `/cart/change?id=<variant>&quantity=0`: guarded
  navigations that add and remove a cart line.
- `/cart.js`: the cart lines and the store's own item subtotal.
Reads run as fetch() calls inside the page. Every navigation goes through the guarded goto(),
and each cart change runs the guard on the current page first.

Speed (2026-10-18): search uses the store's predictive search JSON and the product JSON, so no
search or product page loads; the cart takes all lines in one /cart/add.js request. A lane loads
two pages: the cart at open (the origin for the JSON calls) and the cart again to show it.

A store that blocks these URLs (EcoQuality returns 404 for all of them) needs its own adapter.
Checkout is one page with the payment fields on it, so the guard stops on that page: the handoff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote_plus, urlparse

from playwright.async_api import Page

from app.config import CheckoutContact
from app.contracts import CartLine, SupplierRef
from app.lanes.live.adapter_base import (
    AdapterStepError,
    CartRow,
    PlaywrightAdapterLane,
    ProductPage,
    ResultCard,
)
from app.lanes.live.config import LiveLaneSettings

# Product links on a search page: the title from the link, an image alt, or the card heading.
RESULTS_JS = """() => {
  const seen = new Map();
  for (const a of document.querySelectorAll('a[href*="/products/"]')) {
    const url = new URL(a.getAttribute('href'), location.href);
    const handle = url.pathname.split('/products/')[1]?.split('/')[0];
    if (!handle) continue;
    const card = a.closest('li, article, [class*="card" i], [class*="product" i]') || a;
    const title = (a.innerText || a.getAttribute('aria-label') || a.querySelector('img')?.alt
                   || card.querySelector('h2, h3, [class*="title" i]')?.innerText || '').trim();
    const prev = seen.get(handle);
    if (!prev || (!prev.title && title)) {
      seen.set(handle, {handle, title, url: url.origin + '/products/' + handle,
                        snippet: card.innerText.replace(/\\s+/g, ' ').slice(0, 300)});
    }
  }
  return [...seen.values()].filter(r => r.title.length > 3).slice(0, 12);
}"""
JSON_JS = "url => fetch(url, {headers: {Accept: 'application/json'}}).then(r => r.ok ? r.json() : null)"
POST_JS = """([url, body]) => fetch(url, {method: 'POST', body: JSON.stringify(body),
  headers: {'Content-Type': 'application/json', Accept: 'application/json'}}).then(r => r.status)"""
TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ShopifyStore:
    name: str  # the adapter name in discovery.ADAPTER_DOMAINS
    free_shipping_threshold: float | None = None
    minimum_order_value: float = 0.0


WEES_EXPRESS = ShopifyStore(name="wees_express")
# Checked 2026-10-18: /search/suggest.json, /products/<handle>.js and /cart.js answer for a guest.
RESTAURANTWARE = ShopifyStore(name="restaurantware")


class ShopifyAdapterLane(PlaywrightAdapterLane):
    single_page_checkout = True
    card_listing_is_full = True  # the product JSON is the whole listing
    cart_rows_on_any_page = True  # /cart.js answers on any page of the store
    nav_settle_ms = 0  # pages here are read through JSON, not through late-rendered markup
    tab_per_item = False  # searches are JSON calls on the store page, not page loads

    def __init__(self, supplier: SupplierRef, store: ShopifyStore, cfg: LiveLaneSettings | None = None):
        super().__init__(supplier, cfg)
        self.store = store
        self.free_shipping_threshold = store.free_shipping_threshold
        self.minimum_order_value = store.minimum_order_value
        self._query = ""  # the last page search (the page-search fallback path)
        self._query_by_handle: dict[str, str] = {}  # items search at once: each product keeps its query
        self._variant_ids: dict[str, int] = {}  # cart SKU -> variant id, for removal

    def _base(self) -> str:
        u = urlparse(self.supplier.url)
        return f"{u.scheme}://{u.netloc}"

    def open_url(self) -> str:
        return self.cart_url()  # small page; the store origin the JSON calls need

    # ---- search ------------------------------------------------------------------

    async def search_cards(self, page: Page, query: str) -> list[ResultCard]:
        self._query = query
        url = (f"{self._base()}/search/suggest.json?q={quote_plus(query)}"
               "&resources[type]=product&resources[limit]=10&resources[options][unavailable_products]=last")
        data = await page.evaluate(JSON_JS, url)
        found = ((data or {}).get("resources") or {}).get("results", {}).get("products") or []
        if not found:  # predictive search off or empty: the search page
            cards = await super().search_cards(page, query)
        else:
            cards = [ResultCard(title=r["title"], url=f"{self._base()}/products/{r['handle']}",
                                snippet=f"{r['title']} ${r.get('price', '')} {r.get('vendor', '')}".strip())
                     for r in found if r.get("handle")]
        for card in cards:
            self._query_by_handle.setdefault(_handle(card.url), query)
        return cards

    async def card_listing(self, page: Page, card: ResultCard) -> ProductPage | None:
        data = await self._json(page, f"/products/{_handle(card.url)}.js")
        return self._listing(data, card.url) if data and data.get("variants") else None

    def search_url(self, query: str) -> str:
        self._query = query  # product_extract picks the variant that matches it
        return f"{self._base()}/search?type=product&q={quote_plus(query)}"

    async def result_extract(self, page: Page) -> list[ResultCard]:
        rows = await page.evaluate(RESULTS_JS)
        return [ResultCard(title=r["title"], snippet=r["snippet"], url=r["url"]) for r in rows]

    async def product_extract(self, page: Page) -> ProductPage:
        handle = _handle(page.url)
        data = await self._json(page, f"/products/{handle}.js")
        if not data or not data.get("variants"):
            raise AdapterStepError(f"no product data for {handle}")
        return self._listing(data, page.url)

    def _listing(self, data: dict, url: str) -> ProductPage:
        query = self._query_by_handle.get(_handle(url), self._query)
        variant = pick_variant(data["variants"], query, _variant_param(url))
        title = data["title"] if variant.get("title") in (None, "", "Default Title") else (
            f"{data['title']} - {variant['title']}")
        options = "; ".join(f"{v.get('title')}: ${v['price'] / 100:.2f}" for v in data["variants"])
        description = re.sub(r"\s+", " ", TAGS.sub(" ", data.get("description") or ""))[:2500]
        stock = "In stock" if variant.get("available") else "Sold out"
        return ProductPage(
            title=title,
            sku=_sku(variant),
            price=variant["price"] / 100,
            text=f"{title}\n{stock}\nOptions: {options}\n{description}",
            # The variant goes in the URL: the cart line adds exactly this variant.
            url=f"{self._base()}/products/{_handle(url)}?variant={variant['id']}",
        )

    # ---- cart --------------------------------------------------------------------

    def cart_url(self) -> str:
        return f"{self._base()}/cart"

    async def add_to_cart(self, page: Page, pack_quantity: int) -> None:
        variant = _variant_param(page.url)
        if variant is None:
            data = await self._json(page, f"/products/{_handle(page.url)}.js")
            if not data:
                raise AdapterStepError("no product data to add")
            variant = pick_variant(data["variants"], "", None)["id"]
        await self.goto(f"{self._base()}/cart/add?id={variant}&quantity={pack_quantity}")

    async def add_lines(self, page: Page, lines: list[CartLine]) -> bool:
        items = []
        for line in lines:
            variant = _variant_param(line.url or "")
            if variant is None:
                return False  # no variant in the URL: add from each product page instead
            items.append({"id": variant, "quantity": line.pack_quantity})
        await self.after_nav(page)  # the guard on the current page before the cart changes
        status = await page.evaluate(POST_JS, [f"{self._base()}/cart/add.js", {"items": items}])
        if status != 200:
            raise AdapterStepError(f"cart add refused (HTTP {status})")
        return True

    async def cart_rows(self, page: Page) -> list[CartRow]:
        cart = await self._json(page, "/cart.js")
        if cart is None:
            raise AdapterStepError("no cart data")
        rows = []
        for item in cart.get("items", []):
            sku = item.get("sku") or str(item["variant_id"])
            self._variant_ids[sku] = item["variant_id"]
            rows.append(CartRow(sku=sku, pack_quantity=item["quantity"], unit_price=item["final_price"] / 100))
        return rows

    async def remove_item(self, page: Page, sku: str) -> None:
        variant = self._variant_ids.get(sku)
        if variant is None:
            raise AdapterStepError(f"no cart line for {sku}")
        await self.after_nav(page)
        status = await page.evaluate(POST_JS, [f"{self._base()}/cart/change.js", {"id": str(variant), "quantity": 0}])
        if status != 200:
            raise AdapterStepError(f"cart remove refused (HTTP {status})")

    async def cart_summary(self, page: Page, skus: list[str]) -> str:
        # The store's own cart figures, not the theme's summary block: themes mix promo text
        # ("free on orders of $150+") into it, and J7 once read that $150 as shipping.
        # A Shopify cart has no shipping or tax before checkout, so the cart is `estimated`.
        cart = await self._json(page, "/cart.js")
        if not cart:
            raise AdapterStepError("no cart data")
        lines = [f"Subtotal ${cart['items_subtotal_price'] / 100:.2f}"]
        for d in cart.get("cart_level_discount_applications") or []:
            lines.append(f"Discount ({d.get('title', '')}) -${d['total_allocated_amount'] / 100:.2f}")
        lines.append(f"Cart total ${cart['total_price'] / 100:.2f}")
        lines.append("Shipping and taxes calculated at checkout")
        return "\n".join(lines)

    # ---- checkout ----------------------------------------------------------------

    async def checkout_entry(self, page: Page) -> None:
        await self.click(page, "[name=checkout]", wait_nav=True)

    async def fill_contact(self, page: Page, contact: CheckoutContact) -> list[str]:
        raise AdapterStepError("one-page checkout: the guard stops on the first checkout page")

    async def next_checkout_step(self, page: Page) -> bool:
        return False

    async def _json(self, page: Page, path: str) -> dict | None:
        return await page.evaluate(JSON_JS, f"{self._base()}{path}")


def pick_variant(variants: list[dict], query: str, wanted: int | None) -> dict:
    """The variant in the URL, else the in-stock one whose title words appear in the query
    (a size such as "Medium"), else the first in stock, else the first."""
    if wanted is not None:
        for v in variants:
            if v["id"] == wanted:
                return v
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    in_stock = [v for v in variants if v.get("available", True)] or variants
    scored = [(sum(w in words for w in re.findall(r"[a-z0-9]+", (v.get("title") or "").lower())), i, v)
              for i, v in enumerate(in_stock)]
    best = max(scored, key=lambda t: (t[0], -t[1]))
    return best[2]


def _sku(variant: dict) -> str:
    return (variant.get("sku") or "").strip() or str(variant["id"])


def _handle(url: str) -> str:
    path = urlparse(url).path
    if "/products/" not in path:
        raise AdapterStepError(f"not a product page: {url}")
    return path.split("/products/")[1].split("/")[0]


def _variant_param(url: str) -> int | None:
    value = parse_qs(urlparse(url).query).get("variant", [None])[0]
    return int(value) if value and value.isdigit() else None
