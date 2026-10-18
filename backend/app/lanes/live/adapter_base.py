"""Scripted Playwright lanes for known sites (spec §5 "Adapters").

A subclass gives the site steps:
    search_url / result_selector (+ result_extract) / product_extract / add_to_cart /
    cart_url / cart_rows / remove_item / checkout_entry / fill_contact / next_checkout_step
This base class runs them in the lane order and calls the same judgments as the agent
tools: J4 (j4_rerank), J5 (j5_listing), J6 (ctx.guard) and J7 (j7_totals +
build_verified_cart). Code computes every number.

Every click and every fill goes through `click()` / `fill()`, which run the guard first.
Every navigation goes through `goto()`, which runs the guard after the page loads.

When a site step fails (a selector changed, a timeout), the lane hands the rest of the job
to the Browser Use agent on the same browser, if the agent lane is available.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from app.config import CheckoutContact
from app.contracts import (
    CartLine,
    CheckoutHandoff,
    Evidence,
    GuardVerdict,
    Product,
    SearchSpec,
    ShoppingItem,
    SupplierOffers,
    SupplierRef,
    VerifiedCart,
)
from app.guard import is_card_field
from app.judgments import j4_rerank, j5_listing, j7_totals
from app.lanes.base import LaneContext, LaneGuardStopped
from app.lanes.live import guards, warm
from app.lanes.live.config import LiveLaneSettings, get_live_settings
from app.lanes.live.session import LaneBrowser
from app.stages.extract import money_candidates
from app.stages.totals import build_verified_cart

log = logging.getLogger(__name__)

PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")


# Checkout forms want the state's name ("California"), not its code.
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def pair_labels(text: str) -> str:
    """Put each amount on the line of its label: "Shipping\n$26.85" -> "Shipping $26.85".
    money_candidates() reads a label from the amount's own line."""
    out: list[str] = []
    pending: list[str] = []
    for line in (t.strip() for t in text.splitlines()):
        if not line:
            continue
        if PRICE.search(line):
            out.append(" ".join([*pending, line]))
            pending = []
        else:
            pending.append(line)
    return "\n".join(out)


class AdapterStepError(Exception):
    """A scripted step did not work on this page (selector missing, unexpected layout)."""


class CardFieldRefused(Exception):
    """Code refused to type into a payment field. This must never happen in a correct adapter."""


@dataclass
class ResultCard:
    title: str
    snippet: str
    url: str


@dataclass
class ProductPage:
    title: str
    sku: str
    price: float
    text: str  # the listing text J5 reads (title, description, pack info, stock)
    url: str | None = None  # the product URL when it differs from the page (a variant, a card link)


@dataclass
class CartRow:
    sku: str
    pack_quantity: int
    unit_price: float


def parse_price(text: str) -> float:
    match = PRICE.search(text or "")
    if not match:
        raise AdapterStepError(f"no price in {text!r}")
    return float(match.group(1).replace(",", ""))


class PlaywrightAdapterLane:
    mode: Literal["adapter"] = "adapter"

    # ---- site description (override in a subclass) -------------------------------
    result_selector: str = ""
    result_title_selector: str = "a"
    result_snippet_selector: str | None = None
    cart_totals_selector: str | None = None  # the order summary block; J7 reads only this
    minimum_order_value: float = 0.0
    free_shipping_threshold: float | None = None
    max_results_opened: int = 3
    max_checkout_steps: int = 4
    allow_agent_fallback: bool = True
    # One-page checkouts (Shopify) put the payment fields on the first checkout page, so the
    # guard stops there before any contact step. Reaching that page is the handoff.
    single_page_checkout: bool = False
    # Speed. A card listing that is "full" has everything the product page would (Shopify's
    # product JSON): J5 judges it and no product page opens. A partial one (a search card) opens
    # the product page only when J5 is not sure. None keeps cfg.nav_settle_ms after each load.
    card_listing_is_full: bool = False
    nav_settle_ms: int | None = None
    # True when cart_rows() works from any page of the site (it reads data, not the cart page).
    cart_rows_on_any_page: bool = False
    # True: each item searches in its own tab. False: all items search at once on the lane's
    # page (an adapter that reads site data and loads no search pages).
    tab_per_item: bool = True
    # Add cart lines at the same time. Off by default: a cart kept in the browser (the test site's
    # localStorage) lost a line when two adds raced. Turn on only for a server-side cart checked
    # live; add_to_cart must then not need the tab in front (see click(js=True)).
    parallel_cart_adds: bool = False
    # The verify phase takes this site through checkout to read the real total (quote()). Off for
    # a site whose checkout cannot be used before approval (a captcha, a strict guest form).
    quote_at_checkout: bool = True

    def __init__(self, supplier: SupplierRef, cfg: LiveLaneSettings | None = None):
        self.supplier = supplier
        self.cfg = cfg or get_live_settings()
        self.browser = LaneBrowser(self.cfg, label=supplier.id)
        self.ctx: LaneContext | None = None
        self._agent: Any = None
        self._shots = 0
        self._cart_touched = False  # a new lane's browser has an empty cart: no need to check it
        self._want_quote = False  # set by quote(): read the payment page's order summary
        self._quote_text: str | None = None

    # ---- site steps (override) ----------------------------------------------------

    def search_url(self, query: str) -> str:
        raise NotImplementedError

    def cart_url(self) -> str:
        raise NotImplementedError

    async def result_extract(self, page: Page) -> list[ResultCard]:
        cards: list[ResultCard] = []
        for el in await page.locator(self.result_selector).all():
            link = el.locator(self.result_title_selector).first
            if await link.count() == 0:
                continue
            href = await link.get_attribute("href") or ""
            snippet = ""
            if self.result_snippet_selector:
                sn = el.locator(self.result_snippet_selector).first
                snippet = (await sn.inner_text()) if await sn.count() else ""
            cards.append(ResultCard(title=(await link.inner_text()).strip(), snippet=snippet.strip(),
                                    url=_abs(page.url, href)))
        return cards

    async def product_extract(self, page: Page) -> ProductPage:
        raise NotImplementedError

    async def add_to_cart(self, page: Page, pack_quantity: int) -> None:
        raise NotImplementedError

    async def cart_rows(self, page: Page) -> list[CartRow]:
        raise NotImplementedError

    async def remove_item(self, page: Page, sku: str) -> None:
        raise NotImplementedError

    async def checkout_entry(self, page: Page) -> None:
        raise NotImplementedError

    async def fill_contact(self, page: Page, contact: CheckoutContact) -> list[str]:
        raise NotImplementedError

    async def next_checkout_step(self, page: Page) -> bool:
        """Click the button to the next checkout step. Return False when there is none."""
        raise NotImplementedError

    def checkout_url(self) -> str | None:
        """A URL that opens checkout for the current cart. None: load the cart and use checkout_entry()."""
        return None

    def open_url(self) -> str | None:
        """The page open() loads. None: start the browser only; search loads the first page."""
        return self.supplier.url

    async def search_cards(self, page: Page, query: str) -> list[ResultCard]:
        """Result cards for a query. Default: load search_url(query) and read result_extract()."""
        page = await self.goto(self.search_url(query), page)
        return await self.result_extract(page)

    async def card_listing(self, page: Page, card: ResultCard) -> ProductPage | None:
        """A listing built from the search card (or site data) without opening the product page.
        None: open the product page. See card_listing_is_full."""
        return None

    async def add_lines(self, page: Page, lines: list[CartLine]) -> bool:
        """Add several cart lines at once (one request). False: add them one product page each."""
        return False

    async def checkout_summary(self, page: Page) -> str:
        """The order summary on the payment page: subtotal, shipping, tax, total. Default: the
        page text from the first "Subtotal" through the first "Total" line, labels paired."""
        lines = pair_labels((await self.browser.snapshot(page)).visible_text).splitlines()
        start = next((i for i, t in enumerate(lines) if t.lower().startswith("subtotal")), None)
        if start is None:
            return ""
        end = next((i for i in range(start, len(lines)) if re.match(r"(order |grand )?total\b", lines[i], re.I)),
                   len(lines) - 1)
        return "\n".join(lines[start:end + 1])

    async def fill_contact_on_payment_page(self, page: Page, contact: CheckoutContact) -> None:
        """One-page checkouts only: type the shipping contact on the page that also holds the
        card fields, so the summary shows shipping and tax. Never a card field, never a click."""
        raise AdapterStepError("this site has no contact fields on its payment page")

    async def cart_summary(self, page: Page, skus: list[str]) -> str:
        """The order summary text that J7 reads. Default: `cart_totals_selector`, else the page
        text without the cart lines. Override to show J7 only the amounts the site applies."""
        if self.cart_totals_selector:
            return await self.browser.text(self.cart_totals_selector, page)
        text = (await self.browser.snapshot(page)).visible_text
        return "\n".join(t for t in text.splitlines() if not any(s in t for s in skus))

    # ---- guarded browser actions (use these in the site steps) ----------------------

    def _need_ctx(self) -> LaneContext:
        assert self.ctx is not None, "open() was not called"
        return self.ctx

    async def goto(self, url: str, page: Page | None = None) -> Page:
        page = await self.browser.goto(url, page, settle_ms=self.nav_settle_ms)
        await guards.require_safe(self._need_ctx(), await self.browser.snapshot(page))
        return page

    async def click(self, page: Page, selector: str | Locator, *, wait_nav: bool = False, js: bool = False) -> None:
        """Click after the guard. js=True clicks through the element itself (no actionability
        wait), which works in a background tab where a pointer click can time out."""
        await guards.require_safe(self._need_ctx(), await self.browser.snapshot(page))
        loc = page.locator(selector) if isinstance(selector, str) else selector
        if await loc.count() == 0:
            raise AdapterStepError(f"nothing to click: {selector}")

        async def press() -> None:
            if js:
                await loc.first.evaluate("el => el.click()")
            else:
                await loc.first.click()

        if wait_nav:
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await press()
        else:
            await press()
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_load_state("domcontentloaded", timeout=5000)

    async def fill(self, page: Page, selector: str, value: str) -> None:
        await guards.require_safe(self._need_ctx(), await self.browser.snapshot(page))
        loc = page.locator(selector).first
        if await loc.count() == 0:
            raise AdapterStepError(f"no field: {selector}")
        await _refuse_card_field(loc)
        await loc.fill(value)

    async def settle(self, page: Page, timeout_ms: int = 8000) -> None:
        """Wait for a checkout page to finish loading before the guard reads it. Payment fields
        often arrive late in iframes: Shopify's card iframes appeared about 3 s after
        DOMContentLoaded, so a check at load time saw no card field (seen 2026-10-17)."""
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)

    async def after_nav(self, page: Page, *, allow_stop: bool = False) -> GuardVerdict:
        snap = await self.browser.snapshot(page)
        if allow_stop:
            return await guards.check(self._need_ctx(), snap)
        return await guards.require_safe(self._need_ctx(), snap)

    async def shot(self, tag: str, page: Page | None = None) -> str | None:
        self._shots += 1
        return await self.browser.screenshot(self._need_ctx(), f"{self._shots:03d}_{tag}.jpg", page)

    # ---- SupplierLane ---------------------------------------------------------------

    async def open(self, ctx: LaneContext) -> None:
        self.ctx = ctx
        # A browser started while the owner reviewed the list (warm.py), else this lane's own.
        if (warmed := await warm.claim(self.supplier)) is not None:
            self.browser = warmed
        await self.browser.start()
        ctx.session_id = self.browser.session_id
        ctx.live_url = self.browser.live_url
        if (url := self.open_url()) is not None:
            page = await self.goto(url)
            await self.shot("open", page)

    async def search(self, ctx: LaneContext, items: list[ShoppingItem]) -> SupplierOffers:
        self.ctx = ctx
        if self._agent is not None:
            return await self._agent.search(ctx, items)
        try:
            return await self._search(ctx, items)
        except (AdapterStepError, PlaywrightError) as exc:
            agent = await self._fallback(ctx, exc, "search")
            if agent is None:
                return SupplierOffers(supplier_id=self.supplier.id, status="failed", error=f"adapter: {exc}")
            return await agent.search(ctx, items)

    async def _search(self, ctx: LaneContext, items: list[ShoppingItem]) -> SupplierOffers:
        # Each item searches in its own tab at the same time (same context, so the same cart).
        main = await self.browser.page()
        extra = len(items) - 1 if self.tab_per_item else 0
        tabs = [main] + [await self.browser.new_tab() for _ in range(extra)] + [main] * (len(items) - 1 - extra)
        try:
            results = await asyncio.gather(*(self._search_item(ctx, item, tab)
                                             for item, tab in zip(items, tabs, strict=True)))
        finally:
            for tab in tabs[1:]:
                if tab is not main:
                    with contextlib.suppress(PlaywrightError):
                        await tab.close()
        products = [product for product, _, _ in results if product is not None]
        visited = [url for _, urls, _ in results for url in urls]
        shots = [shot for _, _, shot in results if shot is not None]
        return SupplierOffers(
            supplier_id=self.supplier.id,
            source="web",
            status="ok" if len(products) == len(items) else ("partial" if products else "not_found"),
            products=products,
            minimum_order_value=self.minimum_order_value,
            free_shipping_threshold=self.free_shipping_threshold,
            evidence=Evidence(visited_urls=list(dict.fromkeys(visited)), screenshot_urls=shots),
        )

    async def _search_item(
        self, ctx: LaneContext, item: ShoppingItem, page: Page
    ) -> tuple[Product | None, list[str], str | None]:
        th = ctx.settings.thresholds
        spec = ctx.specs.get(item.id) or item.spec or SearchSpec(
            item_id=item.id, query=item.name, unit=item.unit, spec_fallback=True)
        cards = await self.search_cards(page, spec.query)
        visited = [page.url]
        if not cards:
            return None, visited, None
        ranked = await j4_rerank(ctx.decider, spec, [{"title": c.title, "snippet": c.snippet} for c in cards],
                                 job_id=ctx.job_id)
        best: Product | None = None
        for idx, _p in ranked[: self.max_results_opened]:
            card = cards[idx]
            product = None
            listing = await self.card_listing(page, card)
            if listing is not None:
                product = await self._judge(ctx, spec, item, listing, listing.url or card.url)
            if listing is None or (not self.card_listing_is_full and _p_same(product) < th.match_act):
                page = await self.goto(card.url, page)  # J5 is not sure from the card: read the page
                visited.append(page.url)
                listing = await self.product_extract(page)
                product = await self._judge(ctx, spec, item, listing, listing.url or page.url)
            if best is None or _p_same(product) > _p_same(best):
                best = product
            if _p_same(product) >= th.match_act:
                break
        shot = await self.shot(f"product_{item.id}", page) if best is not None else None
        return best, visited, shot

    async def _judge(self, ctx: LaneContext, spec: SearchSpec, item: ShoppingItem, listing: ProductPage,
                     url: str) -> Product:
        jev, candidates, units = await j5_listing(ctx.decider, spec, listing.title, listing.text, job_id=ctx.job_id)
        rel = self.browser.write_evidence(
            ctx, f"listing_{item.id}_{listing.sku}.json", {"url": url, "title": listing.title, "text": listing.text})
        return Product(
            item_id=item.id, title=listing.title, sku=listing.sku, url=url, raw_listing=rel,
            pack_candidates=candidates, pack_price=listing.price, units_per_pack=units, jev=jev,
        )

    async def set_cart(self, ctx: LaneContext, lines: list[CartLine]) -> VerifiedCart:
        self.ctx = ctx
        if self._agent is not None:
            return await self._agent.set_cart(ctx, lines)
        try:
            return await self._set_cart(ctx, lines)
        except (AdapterStepError, PlaywrightError) as exc:
            agent = await self._fallback(ctx, exc, "cart")
            if agent is None:
                raise
            return await agent.set_cart(ctx, lines)

    async def _set_cart(self, ctx: LaneContext, lines: list[CartLine]) -> VerifiedCart:
        want = {line.sku: line for line in lines}
        page = await self.browser.page()
        have: set[str] = set()
        if self._cart_touched:  # a later cart (a subset plan): fix what an earlier step left
            if not (self.cart_rows_on_any_page and _same_site(page.url, self.cart_url())):
                page = await self.goto(self.cart_url())
            for row in await self.cart_rows(page):
                line = want.get(row.sku)
                if line is None or line.pack_quantity != row.pack_quantity:
                    await self.remove_item(page, row.sku)
                    if not self.cart_rows_on_any_page:
                        page = await self.goto(self.cart_url())
            have = {row.sku for row in await self.cart_rows(page)}
        self._cart_touched = True
        missing = [line for line in lines if line.sku not in have]
        if missing and not await self.add_lines(page, missing):
            for line in missing:
                if not line.url:
                    raise AdapterStepError(f"no product URL for {line.sku}")
            # Load the product pages and add in parallel tabs. If two adds ever race on the
            # server's cart, the cart-lines check below fails the cart; parallel_cart_adds=False
            # adds one at a time instead.
            tabs = [await self.browser.new_tab() for _ in missing]

            async def add(line: CartLine, tab: Page) -> None:
                await self.goto(line.url, tab)
                await self.add_to_cart(tab, line.pack_quantity)

            try:
                if self.parallel_cart_adds:
                    await asyncio.gather(*(add(line, tab) for line, tab in zip(missing, tabs, strict=True)))
                else:
                    for line, tab in zip(missing, tabs, strict=True):
                        await tab.bring_to_front()  # a background tab is throttled: clicks can time out
                        await add(line, tab)
            finally:
                for tab in tabs:
                    with contextlib.suppress(PlaywrightError):
                        await tab.close()
        await page.bring_to_front()
        page = await self.goto(self.cart_url(), page)
        rows = await self.cart_rows(page)
        shot = await self.shot("cart", page)
        summary = await self.cart_summary(page, list(want))
        self.browser.write_evidence(ctx, f"cart_{self._shots:03d}.txt", summary)
        candidates = money_candidates(summary)
        picks, confs = await j7_totals(ctx.decider, candidates, job_id=ctx.job_id)
        cart = build_verified_cart(
            self.supplier.id, lines, candidates, picks, confs,
            minimum_order_value=self.minimum_order_value, session_id=ctx.session_id, screenshot_url=shot,
            observed_line_prices={r.sku: r.unit_price for r in rows},
        )
        seen = {r.sku: r.pack_quantity for r in rows}
        expected = {line.sku: line.pack_quantity for line in lines}
        if seen != expected:
            cart = cart.model_copy(update={
                "verification_status": "failed",
                "reason": "; ".join(r for r in ["cart_lines_mismatch", cart.reason] if r),
            })
        return cart

    async def checkout(self, ctx: LaneContext, contact: CheckoutContact) -> CheckoutHandoff:
        self.ctx = ctx
        if self._agent is not None:
            return await self._agent.checkout(ctx, contact)
        try:
            return await self._checkout(ctx, contact)
        except (AdapterStepError, PlaywrightError) as exc:
            agent = await self._fallback(ctx, exc, "checkout")
            if agent is None:
                return CheckoutHandoff(supplier_id=self.supplier.id, status="failed", reason=f"adapter: {exc}")
            return await agent.checkout(ctx, contact)

    async def _checkout(self, ctx: LaneContext, contact: CheckoutContact) -> CheckoutHandoff:
        if (direct := self.checkout_url()) is not None:
            # One load instead of cart page + checkout click. Not goto(): its guard raises on a payment
            # page, and a one-page checkout (Restaurantware, 2026-10-18) is one at once. The guard runs
            # just below with the stop allowed, as after the checkout click.
            page = await self.browser.goto(direct, None, settle_ms=self.nav_settle_ms)
        else:
            page = await self.goto(self.cart_url())
            await self.checkout_entry(page)
            page = await self.browser.page()
        await self.settle(page)
        verdict = await self.after_nav(page, allow_stop=True)
        if verdict.stop and self.single_page_checkout and verdict.reason in ("dom_card_field", "payment_step"):
            if self._want_quote:
                await self.fill_contact_on_payment_page(page, contact)
                self._quote_text = await self.checkout_summary(page)
            shot = await self.shot("payment_step", page)
            return CheckoutHandoff(supplier_id=self.supplier.id, status="at_payment", live_url=ctx.live_url,
                                   screenshot_url=shot)
        if verdict.stop:  # the guard stopped before the contact step
            shot = await self.shot("early_stop", page)
            return CheckoutHandoff(supplier_id=self.supplier.id, status="failed",
                                   reason=f"early_stop:{verdict.reason}", screenshot_url=shot)
        filled = await self.fill_contact(page, contact)
        await ctx.emit("checkout_filled", {"fields": filled, "zip": contact.zip})
        await self.shot("shipping", page)
        for _ in range(self.max_checkout_steps):
            try:
                moved = await self.next_checkout_step(page)
            except LaneGuardStopped as stop:  # the guard ran before the click and stopped
                verdict = stop.verdict
                break
            page = await self.browser.page()
            await self.settle(page)
            verdict = await self.after_nav(page, allow_stop=True)
            if verdict.stop or not moved:
                break
        shot = await self.shot("payment_step", page)
        text = (await self.browser.snapshot(page)).visible_text
        self.browser.write_evidence(ctx, "payment_step.txt", text)
        if verdict.stop and verdict.reason in ("dom_card_field", "payment_step"):
            if self._want_quote:  # reading is not acting: the guard stops clicks and typing only
                self._quote_text = await self.checkout_summary(page)
            return CheckoutHandoff(supplier_id=self.supplier.id, status="at_payment", live_url=ctx.live_url,
                                   screenshot_url=shot)
        reason = f"early_stop:{verdict.reason}" if verdict.stop else "payment_step_not_reached"
        return CheckoutHandoff(supplier_id=self.supplier.id, status="failed", reason=reason, screenshot_url=shot)

    async def quote(
        self, ctx: LaneContext, contact: CheckoutContact, lines: list[CartLine]
    ) -> tuple[VerifiedCart | None, CheckoutHandoff]:
        """Take the cart (already set to `lines`) through checkout to the payment page, read the
        real order summary there, and stop. The cart is `verified` when the page shows the
        subtotal, shipping and total and they add up. The browser stays at payment: the handoff."""
        self.ctx = ctx
        self._want_quote, self._quote_text = True, None
        try:
            handoff = await self.checkout(ctx, contact)
        finally:
            self._want_quote = False
        text = self._quote_text
        if handoff.status != "at_payment" or not text:
            return None, handoff
        self.browser.write_evidence(ctx, f"checkout_summary_{self._shots:03d}.txt", text)
        candidates = money_candidates(text)
        picks, confs = await j7_totals(ctx.decider, candidates, job_id=ctx.job_id)
        cart = build_verified_cart(
            self.supplier.id, lines, candidates, picks, confs, minimum_order_value=self.minimum_order_value,
            session_id=ctx.session_id, screenshot_url=handoff.screenshot_url,
        )
        await ctx.emit("checkout_quote", {"status": cart.verification_status,
                                          "total": cart.costs.total if cart.costs else None})
        return cart, handoff

    async def close(self, ctx: LaneContext) -> None:
        await self.browser.close()

    # ---- fallback to the LLM agent on the same browser --------------------------------

    async def _fallback(self, ctx: LaneContext, exc: Exception, step: str) -> Any:
        log.warning("adapter %s failed at %s: %s", self.supplier.id, step, exc)
        if not self.allow_agent_fallback or self.cfg.openrouter_api_key is None:
            return None
        try:
            from app.lanes.live.agent_lane import BrowserUseAgentLane
        except ImportError:
            return None
        agent = BrowserUseAgentLane(self.supplier, self.cfg)
        agent.browser = self.browser  # same browser, same cart
        agent.attach(ctx)
        self._agent = agent
        await ctx.emit("lane_fallback", {"from": "adapter", "to": "llm_agent", "step": step, "error": str(exc)[:200]})
        return agent


def _p_same(product: Product | None) -> float:
    return product.jev.match_probs.get("2", 0.0) if product is not None and product.jev else 0.0


def _same_site(a: str, b: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(a).netloc == urlparse(b).netloc


def _abs(base: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, href)


async def _refuse_card_field(loc: Locator) -> None:
    attrs = await loc.evaluate(
        "el => ({name: el.getAttribute('name'), id: el.id, autocomplete: el.getAttribute('autocomplete'),"
        " label: el.labels && el.labels.length ? el.labels[0].innerText : el.getAttribute('aria-label')})"
    )
    if is_card_field(attrs.get("name"), attrs.get("id"), attrs.get("label"), attrs.get("autocomplete")):
        raise CardFieldRefused(json.dumps(attrs))


__all__ = [
    "AdapterStepError", "CardFieldRefused", "CartRow", "PlaywrightAdapterLane", "ProductPage",
    "ResultCard", "parse_price",
]
