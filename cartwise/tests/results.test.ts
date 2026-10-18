import { describe, expect, it } from "vitest";
import { finalizeResults } from "../src/core/results.js";
import type { CartQuote, PurchasePlan } from "../src/types.js";
import { fixturePipeline } from "./helpers.js";

describe("finalizeResults", () => {
  it("produces the exact fixture winner, baseline, and savings", () => {
    const { offers, plans, quotes } = fixturePipeline();
    const result = finalizeResults(offers, plans, quotes);

    expect(result.winnerPlanId).toBe("plan:markers=supplier-a|tape=supplier-b");
    expect(result.lowestVerifiedTotalCents).toBe(2500);
    expect(result.bestSingleSupplierTotalCents).toBe(3000);
    expect(result.savingsCents).toBe(500);
    expect(result.scope).toBe("all_feasible_verified");
  });

  it("excludes plans affected by an incomplete quote", () => {
    const { offers, plans, quotes, carts } = fixturePipeline();
    const supplierBCombined = carts.find(
      (cart) => cart.supplier.id === "supplier-b" && cart.items.length === 2,
    )!;
    const changedQuotes = quotes.map((quote) =>
      quote.key === supplierBCombined.key
        ? { ...quote, status: "incomplete" as const, error: "Missing tax" }
        : quote,
    );
    const result = finalizeResults(offers, plans, changedQuotes);

    expect(result.winnerPlanId).toBe("plan:markers=supplier-a|tape=supplier-b");
    expect(result.scope).toBe("partial_verified");
    expect(result.plans.find((plan) => plan.quoteKeys.includes(supplierBCombined.key))?.status).toBe(
      "incomplete",
    );
  });

  it("returns null savings when no single-supplier baseline is verified", () => {
    const { offers, plans, quotes, carts } = fixturePipeline();
    const combinedKeys = new Set(
      carts.filter((cart) => cart.items.length === 2).map((cart) => cart.key),
    );
    const changedQuotes = quotes.map((quote) =>
      combinedKeys.has(quote.key)
        ? { ...quote, status: "incomplete" as const, error: "Blocked checkout" }
        : quote,
    );
    const result = finalizeResults(offers, plans, changedQuotes);

    expect(result.lowestVerifiedTotalCents).toBe(2500);
    expect(result.bestSingleSupplierPlanId).toBeNull();
    expect(result.savingsCents).toBeNull();
  });

  it("breaks equal single-supplier totals by plan ID", () => {
    const plan = (id: string, supplierId: string, key: string): PurchasePlan => ({
      id,
      allocations: [{ itemId: "item", supplierId, offerId: `${supplierId}-offer`, packs: 1 }],
      merchandiseSubtotalCents: 100,
      quoteKeys: [key],
      totalCents: null,
      status: "estimated",
      issues: [],
    });
    const quote = (key: string, supplierId: string): CartQuote => ({
      key,
      supplierId,
      lines: [],
      costs: {
        subtotalCents: 100,
        shippingCents: 0,
        taxCents: 0,
        feesCents: 0,
        discountsCents: 0,
        totalCents: 100,
      },
      status: "verified",
      evidence: { runId: null, checkoutUrl: null, screenshotUrl: null },
      error: null,
    });
    const result = finalizeResults(
      [],
      [plan("plan:b", "supplier-b", "cart-b"), plan("plan:a", "supplier-a", "cart-a")],
      [quote("cart-a", "supplier-a"), quote("cart-b", "supplier-b")],
    );

    expect(result.winnerPlanId).toBe("plan:a");
  });

  it("prefers fewer suppliers when verified totals tie", () => {
    const plan = (
      id: string,
      supplierIds: string[],
      quoteKeys: string[],
    ): PurchasePlan => ({
      id,
      allocations: supplierIds.map((supplierId, index) => ({
        itemId: `item-${index}`,
        supplierId,
        offerId: `${supplierId}-offer-${index}`,
        packs: 1,
      })),
      merchandiseSubtotalCents: 100,
      quoteKeys,
      totalCents: null,
      status: "estimated",
      issues: [],
    });
    const quote = (key: string, supplierId: string, totalCents: number): CartQuote => ({
      key,
      supplierId,
      lines: [],
      costs: {
        subtotalCents: totalCents,
        shippingCents: 0,
        taxCents: 0,
        feesCents: 0,
        discountsCents: 0,
        totalCents,
      },
      status: "verified",
      evidence: { runId: null, checkoutUrl: null, screenshotUrl: null },
      error: null,
    });
    const result = finalizeResults(
      [],
      [
        plan("plan:split", ["supplier-a", "supplier-b"], ["cart-a", "cart-b"]),
        plan("plan:single", ["supplier-a", "supplier-a"], ["cart-combined"]),
      ],
      [
        quote("cart-a", "supplier-a", 50),
        quote("cart-b", "supplier-b", 50),
        quote("cart-combined", "supplier-a", 100),
      ],
    );

    expect(result.winnerPlanId).toBe("plan:single");
  });
});
