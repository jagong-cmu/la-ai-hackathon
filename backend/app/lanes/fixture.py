"""Fixture lane: a recorded supplier that runs the real lane code paths without a browser.

It produces DOM-like text, runs the real regex extraction, the real J5/J6/J7 calls
(answered by StubJev with the fixture's expected answers), and the real sum check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import FIXTURES_DIR, CheckoutContact
from app.contracts import (
    CartLine,
    CheckoutHandoff,
    Evidence,
    PageInput,
    PageSnapshot,
    Product,
    SearchSpec,
    ShoppingItem,
    SupplierOffers,
    SupplierRef,
    VerifiedCart,
)
from app.judgments import j5_listing, j7_totals
from app.lanes.base import LaneBlocked, LaneContext
from app.stages.extract import money_candidates
from app.stages.totals import build_verified_cart
from app.stub_rules import FIXTURE_J5


def fixture_path(supplier_id: str) -> Path:
    return FIXTURES_DIR / "suppliers" / f"{supplier_id}.json"


class FixtureLane:
    def __init__(self, supplier: SupplierRef, data: dict[str, Any]):
        self.supplier = supplier
        self.data = data
        self.mode = data.get("mode", "fixture")  # shows the lane kind the fixture stands in for
        self.behavior = data["behavior"]
        self.listings = {listing["itemId"]: listing for listing in data["listings"]}
        self.cart_count = 0
        for listing in data["listings"]:
            FIXTURE_J5[listing["title"]] = listing["jev"]

    @classmethod
    def load(cls, supplier: SupplierRef) -> FixtureLane:
        return cls(supplier, json.loads(fixture_path(supplier.id).read_text()))

    async def open(self, ctx: LaneContext) -> None:
        ctx.session_id = f"fixture-{ctx.job_id}-{self.supplier.id}"
        ctx.live_url = None  # fixtures have no live view; the UI shows the screenshot stream

    async def search(self, ctx: LaneContext, items: list[ShoppingItem]) -> SupplierOffers:
        await ctx.sleep(self.behavior["searchSeconds"] * 0.5)
        if self.behavior.get("blockAt") == "search":
            page = PageSnapshot(url=f"{self.supplier.url}/search",
                                visible_text="Please verify you are human. Complete the CAPTCHA to continue.")
            verdict = await ctx.guard(page)
            raise LaneBlocked(verdict.blocked or "bot_check")
        await ctx.sleep(self.behavior["searchSeconds"] * 0.5)

        products: list[Product] = []
        visited: list[str] = []
        for item in items:
            listing = self.listings.get(item.id)
            if listing is None:
                continue
            spec = ctx.specs.get(item.id) or SearchSpec(item_id=item.id, query=item.name, unit=item.unit,
                                                        must_have=[], spec_fallback=True)
            if not spec.must_have:
                spec = spec.model_copy(update={"must_have": _fixture_must_haves(item)})
            url = f"{self.supplier.url}/p/{listing['sku']}"
            jev, candidates, units = await j5_listing(ctx.decider, spec, listing["title"], listing["text"],
                                                      job_id=ctx.job_id)
            path, rel = ctx.evidence(f"listing_{item.id}.json")
            path.write_text(json.dumps({"url": url, "title": listing["title"], "text": listing["text"]}))
            visited.append(url)
            products.append(Product(
                item_id=item.id, title=listing["title"], sku=listing["sku"], url=url, raw_listing=rel,
                pack_candidates=candidates, pack_price=listing["packPrice"], units_per_pack=units, jev=jev,
            ))
        return SupplierOffers(
            supplier_id=self.supplier.id,
            source="web",
            status="ok" if len(products) == len(items) else ("partial" if products else "not_found"),
            products=products,
            minimum_order_value=self.data["minimumOrderValue"],
            free_shipping_threshold=self.data["freeShippingThreshold"],
            evidence=Evidence(visited_urls=visited),
        )

    async def set_cart(self, ctx: LaneContext, lines: list[CartLine]) -> VerifiedCart:
        first = self.cart_count == 0
        self.cart_count += 1
        await ctx.sleep(self.behavior["cartSeconds"] if first else self.behavior["subsetSeconds"])
        subtotal = round(sum(line.unit_price * line.pack_quantity for line in lines), 2)
        threshold = self.data["freeShippingThreshold"]
        shipping = 0.0 if threshold is not None and subtotal >= threshold else self.data["flatShipping"]
        tax = round(subtotal * self.data["taxRate"], 2)
        total = round(subtotal + shipping + tax, 2)
        page_text = "\n".join([
            "Your cart",
            *[f"{line.sku} x {line.pack_quantity}  ${line.unit_price * line.pack_quantity:.2f}" for line in lines],
            f"Subtotal ({len(lines)} items) ${subtotal:.2f}",
            f"Estimated shipping ${shipping:.2f}",
            f"Estimated tax ${tax:.2f}",
            f"You saved ${subtotal * 0.04:.2f} with web pricing",
            f"Order total ${total:.2f}",
        ])
        await ctx.guard(PageSnapshot(url=f"{self.supplier.url}/cart", visible_text=page_text))
        summary_lines = [line for line in page_text.splitlines() if not line.startswith(tuple(cl.sku for cl in lines))]
        candidates = money_candidates("\n".join(summary_lines))
        picks, confs = await j7_totals(ctx.decider, candidates, job_id=ctx.job_id)
        path, rel = ctx.evidence(f"cart_{self.cart_count}.txt")
        path.write_text(page_text)
        await ctx.emit("screenshot", {"url": rel})
        return build_verified_cart(
            self.supplier.id, lines, candidates, picks, confs,
            minimum_order_value=self.data["minimumOrderValue"],
            session_id=ctx.session_id, screenshot_url=rel,
        )

    async def checkout(self, ctx: LaneContext, contact: CheckoutContact) -> CheckoutHandoff:
        await ctx.sleep(self.behavior["checkoutSeconds"] * 0.5)
        shipping_page = PageSnapshot(
            url=f"{self.supplier.url}/checkout/shipping",
            visible_text="Checkout. Shipping address. Ship to: name, street, city, state, ZIP.",
            inputs=[PageInput(name="address1", autocomplete="address-line1", label="Street address")],
        )
        verdict = await ctx.guard(shipping_page)
        if verdict.stop:
            return CheckoutHandoff(supplier_id=self.supplier.id, status="failed", reason=f"early_stop:{verdict.reason}")
        await ctx.emit("checkout_filled", {"fields": ["name", "address1", "city", "state", "zip", "email", "phone"],
                                           "zip": contact.zip})
        await ctx.sleep(self.behavior["checkoutSeconds"] * 0.5)
        payment_page = PageSnapshot(
            url=f"{self.supplier.url}/checkout/payment",
            visible_text="Payment method. Card number. Expiration. Security code. Place order.",
            inputs=[PageInput(name="cardnumber", autocomplete="cc-number", label="Card number")],
        )
        verdict = await ctx.guard(payment_page)
        path, rel = ctx.evidence("payment_step.txt")
        path.write_text(payment_page.visible_text)
        if not verdict.stop:  # must never happen; fail closed anyway
            return CheckoutHandoff(supplier_id=self.supplier.id, status="failed", reason="guard_did_not_stop")
        return CheckoutHandoff(supplier_id=self.supplier.id, status="at_payment", live_url=ctx.live_url,
                               screenshot_url=rel)

    async def close(self, ctx: LaneContext) -> None:
        return None


def _fixture_must_haves(item: ShoppingItem) -> list[str]:
    # Two must-haves per fixture item, matching the fixture's mustHave answer lists.
    name = item.name.lower()
    if "glove" in name:
        return ["nitrile", "size medium"]
    if "box" in name:
        return ["white bakery box", "10 x 10 x 5 in"]
    if "parchment" in name:
        return ["parchment", "half sheet size"]
    if "bag" in name:
        return ["clear poly bread bag", "8 x 4 x 18 in"]
    return []
