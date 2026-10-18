"""Cart totals: resolve J7's picks into costs, then a code sum check.

A cart is `verified` only when subtotal + shipping + tax + other fees equals the total
within $0.01. A mismatch means the reading is wrong somewhere, so the cart is not trusted.

Many real carts show no total before checkout (a subtotal and a ZIP shipping estimate, tax
"calculated at checkout"), or a "total" with no shipping in it. Such a cart is `estimated`:
code adds up the parts that were shown, flags what was missing, and claims no sum check.
It is never `verified`.
"""

from __future__ import annotations

from app.contracts import CartItem, CartLine, Costs, MoneyCandidate, VerifiedCart, cart_key

TOLERANCE = 0.01


def resolve_costs(
    candidates: list[MoneyCandidate], picks: dict[str, int | None]
) -> tuple[Costs | None, list[str]]:
    flags: list[str] = []

    def value(field: str) -> float | None:
        idx = picks.get(field)
        return candidates[idx].value if idx is not None and 0 <= idx < len(candidates) else None

    subtotal, total = value("subtotal"), value("total")
    if subtotal is None:
        return None, ["subtotal_or_total_missing"]
    shipping = value("shipping")
    if shipping is None:
        shipping = 0.0
        flags.append("shipping_not_shown")
    tax = value("tax")
    if tax is None:
        tax = 0.0
        flags.append("tax_not_shown")
    # Small positive leftovers can be real fees; we only accept them when a label says so.
    # A row already picked as shipping or tax ("Delivery fee", "Taxes and fees") is not counted again.
    picked = {i for i in picks.values() if i is not None}
    fee_labels = [c for i, c in enumerate(candidates) if "fee" in c.label.lower() and i not in picked]
    other_fees = round(sum(c.value for c in fee_labels), 2) if fee_labels else 0.0
    if total is None:  # nothing to check against: add up what the page showed
        flags.append("total_not_shown")
        total = round(subtotal + shipping + tax + other_fees, 2)
        return Costs(subtotal=subtotal, shipping=shipping, tax=tax, other_fees=other_fees, total=total), flags
    other = round(total - subtotal - shipping - tax, 2)
    if abs(other - other_fees) > TOLERANCE:
        flags.append(f"sum_mismatch:{other - other_fees:+.2f}")
    return Costs(subtotal=subtotal, shipping=shipping, tax=tax, other_fees=other_fees, total=total), flags


def build_verified_cart(
    supplier_id: str,
    lines: list[CartLine],
    candidates: list[MoneyCandidate],
    picks: dict[str, int | None],
    confidences: dict[str, float],
    *,
    minimum_order_value: float = 0.0,
    session_id: str | None = None,
    screenshot_url: str | None = None,
    observed_line_prices: dict[str, float] | None = None,
) -> VerifiedCart:
    costs, flags = resolve_costs(candidates, picks)
    items = [
        CartItem(
            item_id=line.item_id,
            sku=line.sku,
            pack_quantity=line.pack_quantity,
            unit_price=line.unit_price,
            line_total=round(line.unit_price * line.pack_quantity, 2),
        )
        for line in lines
    ]
    price_changes = []
    for line in lines:
        seen = (observed_line_prices or {}).get(line.sku)
        if seen is not None and abs(seen - line.unit_price) > TOLERANCE:
            price_changes.append({"sku": line.sku, "expected": line.unit_price, "observed": seen})

    # No shipping shown means the "total" is really a subtotal: the cart cannot be verified.
    partial = "total_not_shown" in flags or "shipping_not_shown" in flags
    consistent = costs is not None and not partial and not any(f.startswith("sum_mismatch") for f in flags)
    if costs is None:
        status, reason = "failed", "totals_unreadable"
    elif partial:
        status, reason = ("price_changed" if price_changes else "estimated"), None
    elif not consistent:
        status, reason = "failed", "totals_mismatch"
    elif price_changes:
        status, reason = "price_changed", None
    else:
        status, reason = "verified", None
    return VerifiedCart(
        cart_key=cart_key(supplier_id, lines),
        supplier_id=supplier_id,
        session_id=session_id,
        cart_items=items,
        money_candidates=candidates,
        costs=costs,
        jev=confidences,
        totals_check="consistent" if consistent else ("mismatch" if costs and not partial else "not_checked"),
        minimum_order_met=costs is None or costs.subtotal >= minimum_order_value,
        verification_status=status,
        reason="; ".join(r for r in [reason, *flags] if r) or None,
        price_changes=price_changes,
        screenshot_url=screenshot_url,
    )
