import type { Allocation, NormalizedOffer, PurchasePlan, ShoppingItem, ShoppingRequest } from "../types.js";

function chooseSupplierOffers(offers: NormalizedOffer[], item: ShoppingItem): NormalizedOffer[] {
  const bySupplier = new Map<string, NormalizedOffer>();

  for (const offer of offers.filter((candidate) => candidate.itemId === item.id)) {
    const current = bySupplier.get(offer.supplierId);
    const offerExcess = offer.packsNeeded * offer.unitsPerPack - item.quantity;
    const currentExcess = current
      ? current.packsNeeded * current.unitsPerPack - item.quantity
      : Number.POSITIVE_INFINITY;

    if (
      !current ||
      offer.merchandiseCostCents < current.merchandiseCostCents ||
      (offer.merchandiseCostCents === current.merchandiseCostCents &&
        (offerExcess < currentExcess ||
          (offerExcess === currentExcess && offer.id.localeCompare(current.id) < 0)))
    ) {
      bySupplier.set(offer.supplierId, offer);
    }
  }

  return [...bySupplier.values()].sort(
    (left, right) =>
      left.supplierId.localeCompare(right.supplierId) || left.id.localeCompare(right.id),
  );
}

function planId(allocations: Allocation[]): string {
  const assignments = allocations
    .map((allocation) => `${allocation.itemId}=${allocation.supplierId}`)
    .sort()
    .join("|");
  return `plan:${assignments}`;
}

export function generatePlans(
  offers: NormalizedOffer[],
  request: ShoppingRequest,
): PurchasePlan[] {
  const requestedItems = [...request.items].sort((left, right) => left.id.localeCompare(right.id));
  const options = requestedItems.map((item) => chooseSupplierOffers(offers, item));

  if (options.some((itemOptions) => itemOptions.length === 0)) {
    return [];
  }

  const plans: PurchasePlan[] = [];

  function enumerate(index: number, selected: NormalizedOffer[]): void {
    if (index === requestedItems.length) {
      const allocations = selected.map<Allocation>((offer) => ({
        itemId: offer.itemId,
        supplierId: offer.supplierId,
        offerId: offer.id,
        packs: offer.packsNeeded,
      }));

      plans.push({
        id: planId(allocations),
        allocations,
        merchandiseSubtotalCents: selected.reduce(
          (total, offer) => total + offer.merchandiseCostCents,
          0,
        ),
        quoteKeys: [],
        totalCents: null,
        status: "estimated",
        issues: [],
      });
      return;
    }

    for (const offer of options[index] ?? []) {
      enumerate(index + 1, [...selected, offer]);
    }
  }

  enumerate(0, []);
  return plans.sort((left, right) => left.id.localeCompare(right.id));
}
