"""Adapter for WebstaurantStore (webstaurantstore.com), checked on the live site 2026-10-17.

- Search: /search/<words>.html. Each result is a [data-testid=productBoxContainer] card.
- Product: the h1 title, the price box, and the item number (the last URL segment, the same
  value as the cart line's data-itemnumber).
- Cart: /cart/. Lines are tbody[data-cart-item-id] rows. The summary shows "Cart Subtotal" and,
  after a ZIP code, the freight options (Ground, Second Day, Next Day). There is no total and no
  tax before checkout, so the cart comes back `estimated` (see stages/totals.py).
- Quantity discounts change the unit price in the cart (1: $48.49, 2-3: $43.99). The cart price
  is the one that counts; a difference shows up as `price_changed`.
- Add to cart fails in a headless browser ("There was an error adding this item"). Run this lane
  with BROWSER_HEADLESS=false or the Browser Use cloud browser.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.config import CheckoutContact
from app.lanes.live.adapter_base import (
    AdapterStepError,
    CartRow,
    PlaywrightAdapterLane,
    ProductPage,
    ResultCard,
    parse_price,
)

CARD = "[data-testid=productBoxContainer]"
BUY_QTY = "form:has(#buyButton) input[name=qty]"
ZIP_INPUT = "[data-testid=zip-code-input]:visible"
LINE = "tbody[data-cart-item-id]"
GUEST_EMAIL = "input[name=email]:not(#email-login)"  # the first email box is the sign-in form
CONTINUE = "[data-testid=guestcheckout-continue-button]"
COMPANY_TYPE = "Restaurant"  # a required select on the shipping step
# Freight options are <button role="radio">; the selected one has aria-checked="true".
PICKED_SHIPPING = "[role=radio][aria-checked=true]"

RESULTS_JS = """cards => cards.slice(0, 12).map(el => {
  const a = el.querySelector('a[href$=".html"]');
  const t = el.querySelector('[data-testid=itemDescription]');
  return {url: a && a.href, title: (t ? t.innerText : '').trim(),
          snippet: el.innerText.replace(/\\s+/g, ' ').slice(0, 300)};
}).filter(r => r.url && r.title)"""

# The summary block J7 reads: the subtotal and the shipping option that is selected, and
# nothing else (the unselected freight options are not prices this cart pays).
SUMMARY_JS = """() => {
  const box = [...document.querySelectorAll('div')].filter(d => d.offsetParent
      && d.innerText.includes('Cart Subtotal') && d.querySelector('[data-testid=checkout-button]'))
    // the block with the freight section first (a mobile copy shows only the subtotal), then the smallest
    .sort((a, b) => (b.innerText.includes('Shipping to') - a.innerText.includes('Shipping to'))
                    || a.innerText.length - b.innerText.length)[0];
  if (!box) return '';
  const lines = [];
  const sub = box.innerText.match(/Cart Subtotal\\s*\\$[\\d,]+\\.\\d{2}/);
  if (sub) lines.push(sub[0].replace(/\\s+/g, ' '));
  const zip = box.innerText.match(/Shipping to\\s*(\\d{5})/);
  if (zip) lines.push('Shipping to ' + zip[1]);
  const picked = box.querySelector('[role=radio][aria-checked=true]');
  let row = picked;  // climb to the smallest element that holds the option's name and price
  while (row && row !== box && !/\\$[\\d,]+\\.\\d{2}/.test(row.innerText)) row = row.parentElement;
  if (row && row !== box) lines.push('Shipping (' + row.innerText.replace(/\\s+/g, ' ').trim() + ')');
  return lines.join('\\n');
}"""


class WebstaurantLane(PlaywrightAdapterLane):
    max_results_opened = 3

    def _base(self) -> str:
        u = urlparse(self.supplier.url)
        return f"{u.scheme}://{u.netloc}"

    # ---- search ------------------------------------------------------------------

    def search_url(self, query: str) -> str:
        return f"{self._base()}/search/{quote(search_slug(query))}.html"

    async def result_extract(self, page: Page) -> list[ResultCard]:
        rows = await page.eval_on_selector_all(CARD, RESULTS_JS)
        return [ResultCard(title=r["title"], snippet=r["snippet"], url=r["url"]) for r in rows]

    async def product_extract(self, page: Page) -> ProductPage:
        sku = item_number(page.url)
        if sku is None or await page.locator(BUY_QTY).count() == 0:
            raise AdapterStepError("not a product page")
        price_box = await page.inner_text("#priceBox")
        main = page.locator("main, #main, body").first
        return ProductPage(
            title=(await page.inner_text("h1")).strip(),
            sku=sku,
            price=parse_price(price_box),
            text=re.sub(r"[ \t]+", " ", await main.inner_text())[:4000],
        )

    # ---- cart --------------------------------------------------------------------

    def cart_url(self) -> str:
        return f"{self._base()}/cart/"

    async def add_to_cart(self, page: Page, pack_quantity: int) -> None:
        await self.fill(page, BUY_QTY, str(pack_quantity))
        try:
            async with page.expect_response(
                lambda r: "/cart/" in r.url and r.request.method == "POST", timeout=15000
            ) as resp:
                await self.click(page, "#buyButton")
            status = (await resp.value).status
        except PlaywrightError as exc:
            raise AdapterStepError(f"add to cart sent no cart request: {exc}") from exc
        if status != 200:  # a headless browser gets a redirect and "There was an error adding..."
            raise AdapterStepError(f"add to cart refused (HTTP {status}); run with a visible browser")

    async def cart_rows(self, page: Page) -> list[CartRow]:
        rows: list[CartRow] = []
        for line in await page.locator(LINE).all():
            sku = await line.locator("tr[data-itemnumber]").first.get_attribute("data-itemnumber")
            qty = int(await line.locator("input.quantityInput").first.input_value() or "0")
            total = parse_price(await line.locator("[data-testid=total-price]").first.inner_text())
            if sku and qty > 0:
                rows.append(CartRow(sku=sku, pack_quantity=qty, unit_price=round(total / qty, 2)))
        return rows

    async def remove_item(self, page: Page, sku: str) -> None:
        line = page.locator(f"{LINE}:has(tr[data-itemnumber='{sku}'])")
        await self.click(page, line.locator("button.deleteCartItemButton"))
        await line.wait_for(state="detached", timeout=10000)

    async def cart_summary(self, page: Page, skus: list[str]) -> str:
        # Freight shows only after a ZIP code. It is the shipping cost this cart really pays.
        zip_box = page.locator(ZIP_INPUT)
        if await zip_box.count():
            await self.fill(page, ZIP_INPUT, self._need_ctx().settings.checkout_contact.zip)
            await self.click(page, page.get_by_role("button", name="Calculate"))
            await page.locator(PICKED_SHIPPING).first.wait_for(state="attached", timeout=10000)
        return await page.evaluate(SUMMARY_JS)

    # ---- checkout ----------------------------------------------------------------

    async def checkout_entry(self, page: Page) -> None:
        await self.click(page, "[data-testid=checkout-button]:visible", wait_nav=True)

    async def fill_contact(self, page: Page, contact: CheckoutContact) -> list[str]:
        """Guest checkout, step 1 of 3 (Shipping & Billing). Field ids are React-generated, so
        the selectors use name and autocomplete. Step 2 (Review & Payment) has the card fields:
        the guard stops there. Mapped from the live page; not yet submitted on the live site."""
        if "NOT SET" in contact.address1:
            # Never send placeholder data to a real store: set CHECKOUT_CONTACT__* first.
            raise AdapterStepError("checkout contact is not set (CHECKOUT_CONTACT__ADDRESS1)")
        if await page.locator(GUEST_EMAIL).count() == 0:
            raise AdapterStepError("not the guest checkout page")
        fields = {
            GUEST_EMAIL: contact.email,
            "input[name='shippingAddress.name']": contact.name,
            "input[name='shippingAddress.companyName']": contact.company,
            "input[name='shippingAddress.city']": contact.city,
            "input[name='shippingAddress.zipCode']": contact.zip,
            "input[name='shippingAddress.phone']": contact.phone,
        }
        for selector, value in fields.items():
            await self.fill(page, selector, value)
        # The street box suggests addresses; Escape closes the list and keeps the typed text.
        await self.fill(page, "input[autocomplete='shipping address-line1']", contact.address1)
        await page.keyboard.press("Escape")
        await self.choose(page, "[data-testid=company-type-select] input[role=combobox]", COMPANY_TYPE)
        await self.choose(page, "input[autocomplete='shipping address-level1']", contact.state)
        return ["email", "name", "company", "company_type", "address1", "city", "state", "zip", "phone"]

    async def next_checkout_step(self, page: Page) -> bool:
        button = page.locator(CONTINUE)
        if await button.count() == 0:
            return False
        await self.click(page, button, wait_nav=True)
        return True

    async def choose(self, page: Page, selector: str, value: str) -> None:
        """Pick an option in a searchable select: type it, then Enter takes the first match."""
        await self.fill(page, selector, value)
        await page.keyboard.press("Enter")


def search_slug(query: str) -> str:
    """The site's search path: "hinged to-go box" -> "hinged-to--go-box" (seen on the site)."""
    words = re.sub(r"[^a-z0-9\- ]+", " ", query.lower()).split()
    return "-".join(w.replace("-", "--") for w in words)


def item_number(url: str) -> str | None:
    m = re.search(r"/([A-Za-z0-9]+)\.html$", urlparse(url).path)
    return m.group(1) if m else None
