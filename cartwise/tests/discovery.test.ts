import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  buildDiscoveryResult,
  discoverProducts,
  type DiscoveryAgentOutput,
} from "../src/browser/discovery.js";
import { MvpShoppingRequestSchema } from "../src/schemas.js";
import type { ShoppingItem, Supplier } from "../src/types.js";

const supplier: Supplier = {
  id: "supplier-a",
  name: "Supplier A",
  url: "https://supplier-a.example",
};

const items: ShoppingItem[] = [
  { id: "markers", description: "Black markers", quantity: 12, unit: "marker" },
  { id: "tape", description: "Clear tape", quantity: 2, unit: "roll" },
];

function offer(
  values: Partial<DiscoveryAgentOutput["offers"][number]> & { itemId: string; productUrl: string },
): DiscoveryAgentOutput["offers"][number] {
  return {
    title: "Observed product",
    sku: null,
    unitsPerPack: 1,
    packUnit: "marker",
    packPriceCents: 100,
    inStock: true,
    matchStatus: "exact",
    ...values,
  };
}

describe("product discovery", () => {
  it("returns the two fixture offers for each fixture supplier", async () => {
    const request = MvpShoppingRequestSchema.parse(
      JSON.parse(
        readFileSync(new URL("../src/fixtures/request.json", import.meta.url), "utf8"),
      ),
    );

    for (const fixtureSupplier of request.suppliers) {
      const result = await discoverProducts(fixtureSupplier, request.items);
      expect(result.offers).toHaveLength(2);
      expect(result.missingItemIds).toEqual([]);
      expect(result.offers.every((candidate) => candidate.matchStatus === "exact")).toBe(true);
    }
  });

  it("selects the exact offer with the lowest fulfillment cost", () => {
    const result = buildDiscoveryResult(
      supplier,
      items.slice(0, 1),
      {
        offers: [
          offer({
            itemId: "markers",
            sku: "SIX-PACK",
            productUrl: "https://supplier-a.example/markers-six",
            unitsPerPack: 6,
            packPriceCents: 500,
          }),
          offer({
            itemId: "markers",
            sku: "TWELVE-PACK",
            productUrl: "https://shop.supplier-a.example/markers-twelve",
            unitsPerPack: 12,
            packPriceCents: 900,
          }),
        ],
        missingItemIds: [],
        errors: [],
      },
      "run-1",
    );

    expect(result.offers).toHaveLength(1);
    expect(result.offers[0]?.sku).toBe("TWELVE-PACK");
    expect(result.missingItemIds).toEqual([]);
  });

  it("rejects unknown items and off-domain URLs and reports no eligible match", () => {
    const result = buildDiscoveryResult(
      supplier,
      items,
      {
        offers: [
          offer({
            itemId: "unknown",
            productUrl: "https://supplier-a.example/unknown",
          }),
          offer({
            itemId: "markers",
            productUrl: "https://malicious.example/markers",
          }),
          offer({
            itemId: "tape",
            productUrl: "https://supplier-a.example/tape",
            packUnit: "roll",
            unitsPerPack: 2,
            matchStatus: "uncertain",
          }),
        ],
        missingItemIds: ["unknown"],
        errors: [],
      },
      null,
    );

    expect(result.missingItemIds).toEqual(["markers", "tape"]);
    expect(result.offers).toHaveLength(1);
    expect(result.offers[0]?.matchStatus).toBe("uncertain");
    expect(result.errors).toEqual(
      expect.arrayContaining([
        "Rejected offer with unknown itemId: unknown",
        "Rejected off-domain product URL for item markers",
        "Ignored unknown missing itemId: unknown",
      ]),
    );
  });
});
