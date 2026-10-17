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
from app.guard import CARD_AUTOCOMPLETE, CARD_FIELD
from app.judgments import j4_rerank, j5_listing, j7_totals
from app.lanes.base import LaneContext, LaneGuardStopped
from app.lanes.live import guards
from app.lanes.live.config import LiveLaneSettings, get_live_settings
from app.lanes.live.session import LaneBrowser
from app.stages.extract import money_candidates
from app.stages.totals import build_verified_cart

log = logging.getLogger(__name__)

PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")


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

    def __init__(self, supplier: SupplierRef, cfg: LiveLaneSettings | None = None):
        self.supplier = supplier
        self.cfg = cfg or get_live_settings()
        self.browser = LaneBrowser(self.cfg, label=supplier.id)
        self.ctx: LaneContext | None = None
        self._agent: Any = None
        self._shots = 0

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

    # ---- guarded browser actions (use these in the site steps) ----------------------

    def _need_ctx(self) -> LaneContext:
        assert self.ctx is not None, "open() was not called"
        return self.ctx

    async def goto(self, url: str) -> Page:
        page = await self.browser.goto(url)
        await guards.require_safe(self._need_ctx(), await self.browser.snapshot(page))
        return page

    async def click(self, page: Page, selector: str | Locator, *, wait_nav: bool = False) -> None:
        await guards.require_safe(self._need_ctx(), await self.browser.snapshot(page))
        loc = page.locator(selector) if isinstance(selector, str) else selector
        if await loc.count() == 0:
            raise AdapterStepError(f"nothing to click: {selector}")
        if wait_nav:
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await loc.first.click()
        else:
            await loc.first.click()
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_load_state("domcontentloaded", timeout=5000)

    async def fill(self, page: Page, selector: str, value: str) -> None:
        await guards.require_safe(self._need_ctx(), await self.browser.snapshot(page))
        loc = page.locator(selector).first
        if await loc.count() == 0:
            raise AdapterStepError(f"no field: {selector}")
        await _refuse_card_field(loc)
        await loc.fill(value)

    async def after_nav(self, page: Page, *, allow_stop: bool = False) -> GuardVerdict:
        snap = await self.browser.snapshot(page)
        if allow_stop:
            return await guards.check(self._need_ctx(), snap)
        return await guards.require_safe(self._need_ctx(), snap)

    async def shot(self, tag: str, page: Page | None = None) -> str | None:
        self._shots += 1
        return await self.browser.screenshot(self._need_ctx(), f"{self._shots:03d}_{tag}.png", page)

    # ---- SupplierLane ---------------------------------------------------------------

    async def open(self, ctx: LaneContext) -> None:
        self.ctx = ctx
        await self.browser.start()
        ctx.session_id = self.browser.session_id
        ctx.live_url = self.browser.live_url
        page = await self.goto(self.supplier.url)
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
        products: list[Product] = []
        visited: list[str] = []
        shots: list[str] = []
        th = ctx.settings.thresholds
        for item in items:
            spec = ctx.specs.get(item.id) or item.spec or SearchSpec(
                item_id=item.id, query=item.name, unit=item.unit, spec_fallback=True)
            page = await self.goto(self.search_url(spec.query))
            visited.append(page.url)
            cards = await self.result_extract(page)
            if not cards:
                continue
            ranked = await j4_rerank(ctx.decider, spec, [{"title": c.title, "snippet": c.snippet} for c in cards],
                                     job_id=ctx.job_id)
            best: Product | None = None
            for idx, _p in ranked[: self.max_results_opened]:
                card = cards[idx]
                page = await self.goto(card.url)
                visited.append(page.url)
                listing = await self.product_extract(page)
                jev, candidates, units = await j5_listing(ctx.decider, spec, listing.title, listing.text,
                                                          job_id=ctx.job_id)
                rel = self.browser.write_evidence(
                    ctx, f"listing_{item.id}_{listing.sku}.json",
                    {"url": page.url, "title": listing.title, "text": listing.text})
                product = Product(
                    item_id=item.id, title=listing.title, sku=listing.sku, url=page.url, raw_listing=rel,
                    pack_candidates=candidates, pack_price=listing.price, units_per_pack=units, jev=jev,
                )
                if best is None or _p_same(product) > _p_same(best):
                    best = product
                if _p_same(product) >= th.match_act:
                    break
            if best is not None:
                products.append(best)
                if (rel := await self.shot(f"product_{item.id}", page)) is not None:
                    shots.append(rel)
        return SupplierOffers(
            supplier_id=self.supplier.id,
            source="web",
            status="ok" if len(products) == len(items) else ("partial" if products else "not_found"),
            products=products,
            minimum_order_value=self.minimum_order_value,
            free_shipping_threshold=self.free_shipping_threshold,
            evidence=Evidence(visited_urls=list(dict.fromkeys(visited)), screenshot_urls=shots),
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
        page = await self.goto(self.cart_url())
        for row in await self.cart_rows(page):
            line = want.get(row.sku)
            if line is None or line.pack_quantity != row.pack_quantity:
                await self.remove_item(page, row.sku)
                page = await self.goto(self.cart_url())
        have = {row.sku for row in await self.cart_rows(page)}
        for line in lines:
            if line.sku in have:
                continue
            if not line.url:
                raise AdapterStepError(f"no product URL for {line.sku}")
            page = await self.goto(line.url)
            await self.add_to_cart(page, line.pack_quantity)
        page = await self.goto(self.cart_url())
        rows = await self.cart_rows(page)
        shot = await self.shot("cart", page)
        if self.cart_totals_selector:
            summary = await self.browser.text(self.cart_totals_selector, page)
        else:
            text = (await self.browser.snapshot(page)).visible_text
            summary = "\n".join(t for t in text.splitlines() if not any(s in t for s in want))
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
        page = await self.goto(self.cart_url())
        await self.checkout_entry(page)
        page = await self.browser.page()
        verdict = await self.after_nav(page, allow_stop=True)
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
            verdict = await self.after_nav(page, allow_stop=True)
            if verdict.stop or not moved:
                break
        shot = await self.shot("payment_step", page)
        text = (await self.browser.snapshot(page)).visible_text
        self.browser.write_evidence(ctx, "payment_step.txt", text)
        if verdict.stop and verdict.reason in ("dom_card_field", "payment_step"):
            return CheckoutHandoff(supplier_id=self.supplier.id, status="at_payment", live_url=ctx.live_url,
                                   screenshot_url=shot)
        reason = f"early_stop:{verdict.reason}" if verdict.stop else "payment_step_not_reached"
        return CheckoutHandoff(supplier_id=self.supplier.id, status="failed", reason=reason, screenshot_url=shot)

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


def _p_same(product: Product) -> float:
    return product.jev.match_probs.get("2", 0.0) if product.jev else 0.0


def _abs(base: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, href)


async def _refuse_card_field(loc: Locator) -> None:
    attrs = await loc.evaluate(
        "el => ({name: el.getAttribute('name'), id: el.id, autocomplete: el.getAttribute('autocomplete'),"
        " label: el.labels && el.labels.length ? el.labels[0].innerText : el.getAttribute('aria-label')})"
    )
    if (attrs.get("autocomplete") or "").lower() in CARD_AUTOCOMPLETE or any(
        CARD_FIELD.search(attrs.get(k) or "") for k in ("name", "id", "label")
    ):
        raise CardFieldRefused(json.dumps(attrs))


__all__ = [
    "AdapterStepError", "CardFieldRefused", "CartRow", "PlaywrightAdapterLane", "ProductPage",
    "ResultCard", "parse_price",
]
