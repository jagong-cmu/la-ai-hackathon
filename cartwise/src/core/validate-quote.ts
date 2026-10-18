import { CartQuoteSchema } from "../schemas.js";
import type { CartQuote, CartRequest } from "../types.js";

function normalizedIdentity(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function titleMatches(expected: string, observed: string): boolean {
  const expectedText = expected.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const observedText = observed.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  if (expectedText.includes(observedText) || observedText.includes(expectedText)) {
    return true;
  }

  const ignored = new Set(["a", "an", "and", "for", "of", "the", "with"]);
  const expectedTokens = new Set(expectedText.split(" ").filter((token) => token && !ignored.has(token)));
  const observedTokens = new Set(observedText.split(" ").filter((token) => token && !ignored.has(token)));
  const matching = [...expectedTokens].filter((token) => observedTokens.has(token)).length;
  return expectedTokens.size > 0 && matching / expectedTokens.size >= 0.7;
}

function rejectedQuote(quote: CartQuote, issues: string[]): CartQuote {
  return CartQuoteSchema.parse({
    ...quote,
    status: "incomplete",
    error: [...new Set([...(quote.error ? [quote.error] : []), ...issues])].join("; "),
  });
}

export function validateQuote(request: CartRequest, quote: CartQuote): CartQuote {
  if (quote.status === "failed") {
    return CartQuoteSchema.parse(quote);
  }

  const issues: string[] = [];
  if (quote.key !== request.key) {
    issues.push("Quote key does not match the cart request");
  }
  if (quote.supplierId !== request.supplier.id) {
    issues.push("Quote supplier does not match the cart request");
  }

  const requestedByOffer = new Map(request.items.map((item) => [item.offerId, item]));
  const observedByOffer = new Map<string, CartQuote["lines"][number]>();

  for (const line of quote.lines) {
    if (!requestedByOffer.has(line.offerId)) {
      issues.push(`Unexpected product in cart: ${line.offerId}`);
      continue;
    }
    if (observedByOffer.has(line.offerId)) {
      issues.push(`Duplicate observed product: ${line.offerId}`);
      continue;
    }
    observedByOffer.set(line.offerId, line);
  }

  for (const item of request.items) {
    const line = observedByOffer.get(item.offerId);
    if (!line) {
      issues.push(`Missing requested product: ${item.offerId}`);
      continue;
    }
    if (line.packs !== item.packs) {
      issues.push(`Incorrect pack quantity for ${item.offerId}`);
    }
    if (item.sku) {
      if (!line.observedSku || normalizedIdentity(item.sku) !== normalizedIdentity(line.observedSku)) {
        issues.push(`SKU mismatch for ${item.offerId}`);
      }
    } else if (!titleMatches(item.title, line.observedTitle)) {
      issues.push(`Product title mismatch for ${item.offerId}`);
    }
  }

  const { subtotalCents, shippingCents, taxCents, feesCents, discountsCents, totalCents } =
    quote.costs;
  if (
    subtotalCents === null ||
    shippingCents === null ||
    taxCents === null ||
    feesCents === null ||
    discountsCents === null ||
    totalCents === null
  ) {
    issues.push("Checkout cost breakdown is incomplete");
  } else {
    const observedLineSubtotal = quote.lines.reduce((total, line) => total + line.lineTotalCents, 0);
    if (observedLineSubtotal !== subtotalCents) {
      issues.push("Observed line totals do not equal the checkout subtotal");
    }

    const calculatedTotal =
      subtotalCents + shippingCents + taxCents + feesCents - discountsCents;
    if (calculatedTotal !== totalCents) {
      issues.push("Checkout cost arithmetic is inconsistent");
    }
  }

  if (quote.status !== "verified") {
    issues.push("Browser agent did not report a complete checkout total");
  }

  return issues.length === 0
    ? CartQuoteSchema.parse({ ...quote, status: "verified", error: null })
    : rejectedQuote(quote, issues);
}
