import { createHash } from "node:crypto";
import { CartRequestSchema } from "../schemas.js";
import type {
  Allocation,
  CartRequest,
  NormalizedOffer,
  PurchasePlan,
  ShippingAddress,
  ShoppingRequest,
} from "../types.js";

function cartKey(
  supplierId: string,
  allocations: Allocation[],
  address: ShippingAddress,
): string {
  const canonical = JSON.stringify({
    supplierId,
    items: allocations
      .map(({ offerId, packs }) => ({ offerId, packs }))
      .sort((left, right) => left.offerId.localeCompare(right.offerId) || left.packs - right.packs),
    address: {
      line1: address.line1,
      city: address.city,
      state: address.state,
      postalCode: address.postalCode,
      country: address.country,
    },
  });

  return `cart-${createHash("sha256").update(canonical).digest("hex")}`;
}

export function buildCartRequests(
  plans: PurchasePlan[],
  request: ShoppingRequest,
  offers: NormalizedOffer[],
): CartRequest[] {
  const suppliers = new Map(request.suppliers.map((supplier) => [supplier.id, supplier]));
  const offersById = new Map(offers.map((offer) => [offer.id, offer]));
  const distinctCarts = new Map<string, CartRequest>();

  for (const plan of plans) {
    const bySupplier = new Map<string, Allocation[]>();

    for (const allocation of plan.allocations) {
      const supplier = suppliers.get(allocation.supplierId);
      const offer = offersById.get(allocation.offerId);
      if (
        !supplier ||
        !offer ||
        offer.supplierId !== allocation.supplierId ||
        offer.itemId !== allocation.itemId ||
        allocation.packs <= 0
      ) {
        throw new Error(`Plan ${plan.id} contains an invalid allocation for ${allocation.itemId}`);
      }

      const allocations = bySupplier.get(supplier.id) ?? [];
      allocations.push(allocation);
      bySupplier.set(supplier.id, allocations);
    }

    const quoteKeys: string[] = [];
    for (const supplierId of [...bySupplier.keys()].sort()) {
      const supplier = suppliers.get(supplierId);
      const allocations = bySupplier.get(supplierId);
      if (!supplier || !allocations) {
        throw new Error(`Unknown supplier ${supplierId}`);
      }

      const sortedAllocations = [...allocations].sort(
        (left, right) => left.offerId.localeCompare(right.offerId) || left.packs - right.packs,
      );
      const key = cartKey(supplierId, sortedAllocations, request.address);
      const cart = CartRequestSchema.parse({
        key,
        supplier,
        items: sortedAllocations.map((allocation) => {
          const offer = offersById.get(allocation.offerId);
          if (!offer) {
            throw new Error(`Unknown offer ${allocation.offerId}`);
          }
          return {
            itemId: allocation.itemId,
            offerId: offer.id,
            title: offer.title,
            sku: offer.sku,
            productUrl: offer.productUrl,
            packs: allocation.packs,
            unitsPerPack: offer.unitsPerPack,
          };
        }),
        address: request.address,
        contact: request.contact,
      });

      distinctCarts.set(key, cart);
      quoteKeys.push(key);
    }

    plan.quoteKeys = quoteKeys;
  }

  return [...distinctCarts.values()].sort((left, right) => left.key.localeCompare(right.key));
}
