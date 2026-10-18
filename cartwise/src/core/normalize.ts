import type { DiscoveryResult, NormalizedOffer, ShoppingRequest } from "../types.js";

export function normalizeOffers(
  discoveries: DiscoveryResult[],
  request: ShoppingRequest,
): NormalizedOffer[] {
  const items = new Map(request.items.map((item) => [item.id, item]));
  const supplierIds = new Set(request.suppliers.map((supplier) => supplier.id));
  const normalized = new Map<string, NormalizedOffer>();

  for (const discovery of discoveries) {
    if (!supplierIds.has(discovery.supplierId)) {
      continue;
    }

    for (const offer of discovery.offers) {
      const item = items.get(offer.itemId);
      if (
        !item ||
        offer.supplierId !== discovery.supplierId ||
        !supplierIds.has(offer.supplierId) ||
        !offer.inStock ||
        offer.matchStatus !== "exact" ||
        offer.unitsPerPack <= 0 ||
        !Number.isInteger(offer.unitsPerPack) ||
        offer.packPriceCents < 0 ||
        !Number.isInteger(offer.packPriceCents) ||
        offer.packUnit.trim().toLowerCase() !== item.unit.trim().toLowerCase()
      ) {
        continue;
      }

      const packsNeeded = Math.ceil(item.quantity / offer.unitsPerPack);
      normalized.set(offer.id, {
        ...offer,
        packsNeeded,
        merchandiseCostCents: packsNeeded * offer.packPriceCents,
      });
    }
  }

  return [...normalized.values()].sort(
    (left, right) =>
      left.itemId.localeCompare(right.itemId) ||
      left.supplierId.localeCompare(right.supplierId) ||
      left.id.localeCompare(right.id),
  );
}
