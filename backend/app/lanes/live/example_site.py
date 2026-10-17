"""Example adapter for the local test site in backend/tests/sites/example_site.

It shows the adapter steps for one site. Copy it for each demo site the team picks at H0.
Do not point it at a real supplier site.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from playwright.async_api import Page

from app.config import CheckoutContact
from app.lanes.live.adapter_base import (
    AdapterStepError,
    CartRow,
    PlaywrightAdapterLane,
    ProductPage,
    parse_price,
)


class ExampleSiteLane(PlaywrightAdapterLane):
    result_selector = ".result"
    result_title_selector = "a.title"
    result_snippet_selector = ".snippet"
    cart_totals_selector = "#totals"
    free_shipping_threshold = 75.0

    def _base(self) -> str:
        return self.supplier.url.rstrip("/")

    def search_url(self, query: str) -> str:
        return f"{self._base()}/search.html?q={quote_plus(query)}"

    def cart_url(self) -> str:
        return f"{self._base()}/cart.html"

    async def product_extract(self, page: Page) -> ProductPage:
        if await page.locator(".product #sku").count() == 0:
            raise AdapterStepError("not a product page")
        return ProductPage(
            title=(await page.inner_text("#title")).strip(),
            sku=(await page.inner_text("#sku")).strip(),
            price=parse_price(await page.inner_text("#price")),
            text=await page.inner_text(".product"),
        )

    async def add_to_cart(self, page: Page, pack_quantity: int) -> None:
        await self.fill(page, "#qty", str(pack_quantity))
        await self.click(page, "#add")
        await page.wait_for_selector("#added:has-text('Added')", timeout=5000)

    async def cart_rows(self, page: Page) -> list[CartRow]:
        rows: list[CartRow] = []
        for tr in await page.locator("tr.line").all():
            rows.append(CartRow(
                sku=(await tr.get_attribute("data-sku")) or "",
                pack_quantity=int((await tr.locator(".qty").inner_text()).strip()),
                unit_price=parse_price(await tr.locator(".unit").inner_text()),
            ))
        return rows

    async def remove_item(self, page: Page, sku: str) -> None:
        await self.click(page, f"button.remove[data-sku='{sku}']")

    async def checkout_entry(self, page: Page) -> None:
        await self.click(page, "#checkout", wait_nav=True)

    async def fill_contact(self, page: Page, contact: CheckoutContact) -> list[str]:
        values = {"name": contact.name, "email": contact.email, "phone": contact.phone,
                  "address1": contact.address1, "city": contact.city, "state": contact.state, "zip": contact.zip}
        for field, value in values.items():
            await self.fill(page, f"#ship [name='{field}']", value)
        return list(values)

    async def next_checkout_step(self, page: Page) -> bool:
        button = page.locator("#continue")
        if await button.count() == 0:
            return False
        await self.click(page, button, wait_nav=True)
        return True
