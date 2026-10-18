import { readFileSync } from "node:fs";
import { buildCartRequests } from "../src/core/cart-requests.js";
import { normalizeOffers } from "../src/core/normalize.js";
import { generatePlans } from "../src/core/optimizer.js";
import { validateQuote } from "../src/core/validate-quote.js";
import {
  CartQuoteSchema,
  DiscoveryResultSchema,
  MvpShoppingRequestSchema,
} from "../src/schemas.js";

function readFixture(name: string): unknown {
  return JSON.parse(
    readFileSync(new URL(`../src/fixtures/${name}`, import.meta.url), "utf8"),
  );
}

export function fixturePipeline() {
  const request = MvpShoppingRequestSchema.parse(readFixture("request.json"));
  const discoveries = DiscoveryResultSchema.array().parse(readFixture("discovery.json"));
  const offers = normalizeOffers(discoveries, request);
  const plans = generatePlans(offers, request);
  const carts = buildCartRequests(plans, request, offers);
  const rawQuotes = CartQuoteSchema.array().parse(readFixture("quotes.json"));
  const cartsByKey = new Map(carts.map((cart) => [cart.key, cart]));
  const quotes = rawQuotes.map((quote) => {
    const cart = cartsByKey.get(quote.key);
    if (!cart) {
      throw new Error(`Quote fixture has no matching cart: ${quote.key}`);
    }
    return validateQuote(cart, quote);
  });

  return { request, discoveries, offers, plans, carts, quotes };
}
