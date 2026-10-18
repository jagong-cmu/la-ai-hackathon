import { describe, expect, it } from "vitest";
import { normalizeOffers } from "../src/core/normalize.js";
import type { DiscoveryResult } from "../src/types.js";
import { fixturePipeline } from "./helpers.js";

describe("normalizeOffers", () => {
  it("calculates required packs and merchandise cost", () => {
    const { offers } = fixturePipeline();
    expect(offers).toHaveLength(4);
    expect(offers.every((offer) => offer.packsNeeded === 1)).toBe(true);
    expect(
      offers.find((offer) => offer.id === "offer-supplier-a-markers")
        ?.merchandiseCostCents,
    ).toBe(800);
  });

  it("rounds pack counts up", () => {
    const { request, discoveries } = fixturePipeline();
    const largerRequest = structuredClone(request);
    largerRequest.items[0]!.quantity = 13;
    const offers = normalizeOffers(discoveries, largerRequest);
    const markers = offers.find((offer) => offer.id === "offer-supplier-a-markers");

    expect(markers?.packsNeeded).toBe(2);
    expect(markers?.merchandiseCostCents).toBe(1600);
  });

  it("excludes mismatched, uncertain, and unavailable offers", () => {
    const { request, discoveries } = fixturePipeline();
    const base = discoveries[0]!.offers[0]!;
    const invalidDiscovery: DiscoveryResult = {
      supplierId: "supplier-a",
      missingItemIds: [],
      errors: [],
      runId: null,
      offers: [
        { ...base, id: "wrong-unit", packUnit: "box" },
        { ...base, id: "uncertain", matchStatus: "uncertain" },
        { ...base, id: "unavailable", inStock: false },
      ],
    };

    expect(normalizeOffers([invalidDiscovery], request)).toEqual([]);
  });
});
