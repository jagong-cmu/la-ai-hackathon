import { describe, expect, it } from "vitest";
import { generatePlans } from "../src/core/optimizer.js";
import { fixturePipeline } from "./helpers.js";

describe("generatePlans", () => {
  it("enumerates all four two-item, two-supplier plans", () => {
    const { plans } = fixturePipeline();
    expect(plans).toHaveLength(4);
    expect(plans.map((plan) => plan.merchandiseSubtotalCents).sort((a, b) => a - b)).toEqual([
      1500, 2500, 3200, 4200,
    ]);
  });

  it("removes infeasible assignments when a supplier lacks a product", () => {
    const { request, offers } = fixturePipeline();
    const withoutSupplierBTape = offers.filter(
      (offer) => !(offer.supplierId === "supplier-b" && offer.itemId === "tape"),
    );

    const plans = generatePlans(withoutSupplierBTape, request);
    expect(plans).toHaveLength(2);
    expect(
      plans.every((plan) =>
        plan.allocations.some(
          (allocation) => allocation.itemId === "tape" && allocation.supplierId === "supplier-a",
        ),
      ),
    ).toBe(true);
  });

  it("fulfills every requested quantity in every feasible plan", () => {
    const { request, offers, plans } = fixturePipeline();
    const items = new Map(request.items.map((item) => [item.id, item]));
    const offersById = new Map(offers.map((offer) => [offer.id, offer]));

    for (const plan of plans) {
      expect(plan.allocations).toHaveLength(request.items.length);
      for (const allocation of plan.allocations) {
        const item = items.get(allocation.itemId)!;
        const offer = offersById.get(allocation.offerId)!;
        expect(allocation.packs * offer.unitsPerPack).toBeGreaterThanOrEqual(item.quantity);
      }
    }
  });
});
