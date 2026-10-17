"""Cart totals: resolve J7's picks into costs, then a code sum check.

A cart is `verified` only when subtotal + shipping + tax + other fees equals the total
within $0.01. A mismatch means the reading is wrong somewhere, so the cart is not trusted.
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
    if subtotal is None or total is None:
        return None, ["subtotal_or_total_missing"]
    shipping = value("shipping")
    if shipping is None:
        shipping = 0.0
        flags.append("shipping_not_shown")
    tax = value("tax")
    if tax is None:
        tax = 0.0
        flags.append("tax_not_shown")
    other = round(total - subtotal - shipping - tax, 2)
    # Small positive leftovers can be real fees; we only accept them when a label says so.
    fee_labels = [c for c in candidates if "fee" in c.label.lower()]
    other_fees = round(sum(c.value for c in fee_labels), 2) if fee_labels else 0.0
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

    consistent = costs is not None and not any(f.startswith("sum_mismatch") for f in flags)
    if costs is None:
        status, reason = "failed", "totals_unreadable"
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
        totals_check="consistent" if consistent else ("mismatch" if costs else "not_checked"),
        minimum_order_met=costs is None or costs.subtotal >= minimum_order_value,
        verification_status=status,
        reason="; ".join(r for r in [reason, *flags] if r) or None,
        price_changes=price_changes,
        screenshot_url=screenshot_url,
    )
