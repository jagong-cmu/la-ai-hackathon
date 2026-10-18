"""Normalize offers: packs needed, cost per needed unit, eligibility, flags.

For each (item, supplier) the cheapest eligible option wins, after the ranking penalty for
J5 doubts (`low_match`, `must_have_unsure:*`). All arithmetic is here, never in a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.config import Thresholds
from app.contracts import NormalizedOption, ShoppingItem, SupplierOffers


def is_doubt(flag: str) -> bool:
    """A J5 answer in the confirm band: eligible, but Jev was not sure."""
    return flag == "low_match" or flag.startswith("must_have_unsure:")


@dataclass
class NormalizeResult:
    best: dict[tuple[str, str], NormalizedOption] = field(default_factory=dict)
    all_options: list[NormalizedOption] = field(default_factory=list)

    def suppliers_for(self, item_id: str) -> list[str]:
        return [s for (i, s) in self.best if i == item_id]

    def lines_for_supplier(self, supplier_id: str) -> list[NormalizedOption]:
        return [o for (i, s), o in self.best.items() if s == supplier_id]


def normalize_option(
    item: ShoppingItem, offers: SupplierOffers, product, th: Thresholds
) -> NormalizedOption:
    flags: list[str] = []
    units = product.units_per_pack
    eligible = True

    if product.jev is not None:
        p_same = product.jev.match_probs.get("2", 0.0)
        if p_same < th.match_min:
            eligible = False
            flags.append("no_match")
        elif p_same < th.match_act:
            flags.append("low_match")
        for attr, p in product.jev.must_have.items():
            if p < th.must_have_min:
                eligible = False
                flags.append(f"must_have_failed:{attr}")
            elif p < th.must_have_act:
                flags.append(f"must_have_unsure:{attr}")
        if product.jev.in_stock < th.in_stock_min:
            eligible = False
            flags.append("out_of_stock")

    if not units or units <= 0:
        eligible = False
        flags.append("pack_unknown")
        packs = 0
        bought = 0
        line_cost = 0.0
    else:
        packs = math.ceil(item.quantity / units)
        bought = packs * units
        line_cost = round(packs * product.pack_price, 2)
        if (bought - item.quantity) / item.quantity > th.overbuy_ratio:
            flags.append("overbuy")

    return NormalizedOption(
        item_id=item.id,
        supplier_id=offers.supplier_id,
        sku=product.sku,
        source=offers.source,
        requested_quantity=item.quantity,
        units_per_pack=units,
        pack_price=product.pack_price,
        packs_needed=packs,
        units_bought=bought,
        overbuy_units=max(0, bought - item.quantity),
        line_cost=line_cost,
        effective_unit_price=round(line_cost / item.quantity, 4) if packs else 0.0,
        flags=flags,
        eligible=eligible,
        risk_penalty=round(line_cost * th.risk_penalty * sum(map(is_doubt, flags)), 2),
        url=product.url,
    )


def normalize(
    items: list[ShoppingItem], offers_list: list[SupplierOffers], th: Thresholds
) -> NormalizeResult:
    by_id = {item.id: item for item in items}
    result = NormalizeResult()
    for offers in offers_list:
        if offers.status not in ("ok", "partial") or offers.bundle_total is not None and not offers.products:
            continue
        for product in offers.products:
            item = by_id.get(product.item_id)
            if item is None:
                continue
            option = normalize_option(item, offers, product, th)
            result.all_options.append(option)
            if not option.eligible:
                continue
            key = (item.id, offers.supplier_id)
            current = result.best.get(key)
            if current is None or (option.line_cost + option.risk_penalty
                                   < current.line_cost + current.risk_penalty):
                result.best[key] = option
    return result
