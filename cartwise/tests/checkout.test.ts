import { describe, expect, it } from "vitest";
import { buildCartQuote, verifyCart, verifySupplierCarts } from "../src/browser/checkout.js";
import { buildBatchCheckoutPrompt, buildCheckoutPrompt } from "../src/browser/prompts.js";
import { fixturePipeline } from "./helpers.js";

describe("checkout verification", () => {
  it("loads and validates exact single-item and multi-item fixture carts", async () => {
    const { carts } = fixturePipeline();
    const single = carts.find((cart) => cart.items.length === 1)!;
    const combined = carts.find((cart) => cart.items.length === 2)!;

    const [singleQuote, combinedQuote] = await Promise.all([
      verifyCart(single),
      verifyCart(combined),
    ]);

    expect(singleQuote.status).toBe("verified");
    expect(combinedQuote.status).toBe("verified");
    expect(combinedQuote.lines).toHaveLength(2);
  });

  it("puts every exact URL and pack count in the checkout prompt", () => {
    const { carts } = fixturePipeline();
    const cart = carts.find((candidate) => candidate.items.length === 2)!;
    const prompt = buildCheckoutPrompt(cart);

    for (const item of cart.items) {
      expect(prompt).toContain(item.productUrl);
      expect(prompt).toContain(`"packagesToAdd":${item.packs}`);
    }
    expect(prompt).toContain("Do not sign in");
    expect(prompt).toContain("submit/place a purchase");
    expect(prompt).toContain(`evidence/${cart.key}.png`);
    expect(prompt).toContain("Exclude the customer's name");
  });

  it("builds one checkout prompt for every cart from one supplier", () => {
    const { carts } = fixturePipeline();
    const supplierCarts = carts.filter((cart) => cart.supplier.id === "supplier-a");
    const prompt = buildBatchCheckoutPrompt(supplierCarts);

    for (const cart of supplierCarts) {
      expect(prompt).toContain(cart.key);
      for (const item of cart.items) expect(prompt).toContain(item.productUrl);
    }
    expect(prompt).toContain("completely empty the current cart");
    expect(prompt).toContain("return exactly one quote for every cartKey");
    expect(prompt).toContain("evidence/<cartKey>.png");
    expect(prompt).toContain("Exclude the customer's name");
  });

  it("loads a supplier's fixture carts as one batch", async () => {
    const { carts } = fixturePipeline();
    const supplierCarts = carts.filter((cart) => cart.supplier.id === "supplier-a");
    const quotes = await verifySupplierCarts(supplierCarts);

    expect(quotes).toHaveLength(supplierCarts.length);
    expect(quotes.every((quote) => quote.supplierId === "supplier-a")).toBe(true);
    expect(quotes.every((quote) => quote.status === "verified")).toBe(true);
  });

  it("preserves run evidence and validates a complete observed quote", () => {
    const { carts, quotes } = fixturePipeline();
    const cart = carts[0]!;
    const fixture = quotes.find((quote) => quote.key === cart.key)!;
    const quote = buildCartQuote(
      cart,
      {
        lines: fixture.lines,
        costs: fixture.costs,
        confirmedAbsentCostFields: [],
        reachedCompleteTotal: true,
        checkoutUrl: "https://supplier-a.example/checkout",
        screenshotUrl: "https://evidence.example/screenshot.png",
        status: "complete",
        error: null,
      },
      "run-checkout-123",
    );

    expect(quote.status).toBe("verified");
    expect(quote.evidence.runId).toBe("run-checkout-123");
  });

  it("keeps unknown costs null and reports an incomplete checkout", () => {
    const { carts } = fixturePipeline();
    const cart = carts[0]!;
    const quote = buildCartQuote(
      cart,
      {
        lines: [],
        costs: {
          subtotalCents: null,
          shippingCents: null,
          taxCents: null,
          feesCents: null,
          discountsCents: null,
          totalCents: null,
        },
        confirmedAbsentCostFields: [],
        reachedCompleteTotal: false,
        checkoutUrl: null,
        screenshotUrl: null,
        status: "incomplete",
        error: "Checkout requires login",
      },
      "run-blocked-123",
    );

    expect(quote.status).toBe("incomplete");
    expect(Object.values(quote.costs)).toEqual([null, null, null, null, null, null]);
    expect(quote.error).toContain("Checkout requires login");
    expect(quote.evidence.runId).toBe("run-blocked-123");
  });

  it("accepts fees and discounts explicitly confirmed absent from a complete summary", () => {
    const { carts, quotes } = fixturePipeline();
    const cart = carts[0]!;
    const fixture = quotes.find((quote) => quote.key === cart.key)!;
    const quote = buildCartQuote(
      cart,
      {
        lines: fixture.lines,
        costs: { ...fixture.costs, feesCents: null, discountsCents: null },
        confirmedAbsentCostFields: ["feesCents", "discountsCents"],
        reachedCompleteTotal: true,
        checkoutUrl: "https://supplier-a.example/checkout",
        screenshotUrl: null,
        status: "complete",
        error: null,
      },
      "run-absent-costs",
    );

    expect(quote.status).toBe("verified");
    expect(quote.costs.feesCents).toBe(0);
    expect(quote.costs.discountsCents).toBe(0);
  });

  it("returns failed when no fixture exists for a cart", async () => {
    const { carts } = fixturePipeline();
    const quote = await verifyCart({ ...carts[0]!, key: "cart-does-not-exist" });

    expect(quote.status).toBe("failed");
    expect(quote.error).toContain("No checkout fixture exists");
  });
});
