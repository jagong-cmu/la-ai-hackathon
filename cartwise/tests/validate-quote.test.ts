import { describe, expect, it } from "vitest";
import { validateQuote } from "../src/core/validate-quote.js";
import type { CartQuote } from "../src/types.js";
import { fixturePipeline } from "./helpers.js";

function markerCartAndQuote() {
  const { carts, quotes } = fixturePipeline();
  const cart = carts.find(
    (candidate) =>
      candidate.supplier.id === "supplier-a" &&
      candidate.items.length === 1 &&
      candidate.items[0]?.itemId === "markers",
  )!;
  const quote = quotes.find((candidate) => candidate.key === cart.key)!;
  return { cart, quote };
}

describe("validateQuote", () => {
  it("accepts a correct cart", () => {
    const { cart, quote } = markerCartAndQuote();
    expect(validateQuote(cart, quote)).toMatchObject({ status: "verified", error: null });
  });

  it("rejects incorrect quantities and unexpected products", () => {
    const { cart, quote } = markerCartAndQuote();
    const wrongQuantity = structuredClone(quote);
    wrongQuantity.lines[0]!.packs = 2;
    expect(validateQuote(cart, wrongQuantity)).toMatchObject({ status: "incomplete" });

    const extraProduct = structuredClone(quote);
    extraProduct.lines.push({
      offerId: "unexpected",
      observedTitle: "Unexpected item",
      observedSku: null,
      packs: 1,
      lineTotalCents: 100,
    });
    expect(validateQuote(cart, extraProduct).error).toContain("Unexpected product");
  });

  it("keeps missing monetary values null and marks the quote incomplete", () => {
    const { cart, quote } = markerCartAndQuote();
    const missingShipping: CartQuote = {
      ...structuredClone(quote),
      costs: { ...quote.costs, shippingCents: null },
    };
    const result = validateQuote(cart, missingShipping);
    expect(result.status).toBe("incomplete");
    expect(result.costs.shippingCents).toBeNull();
  });

  it("rejects inconsistent checkout arithmetic", () => {
    const { cart, quote } = markerCartAndQuote();
    const incorrectTotal = structuredClone(quote);
    incorrectTotal.costs.totalCents = quote.costs.totalCents! + 1;
    const result = validateQuote(cart, incorrectTotal);
    expect(result.status).toBe("incomplete");
    expect(result.error).toContain("arithmetic");
  });
});
