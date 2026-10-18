import { readFileSync } from "node:fs";
import { verifyCart } from "../src/browser/checkout.js";
import { buildCartRequests } from "../src/core/cart-requests.js";
import { normalizeOffers } from "../src/core/normalize.js";
import { generatePlans } from "../src/core/optimizer.js";
import { finalizeResults } from "../src/core/results.js";
import {
  DiscoveryResultSchema,
  MvpShoppingRequestSchema,
} from "../src/schemas.js";

function fixture(name: string): unknown {
  return JSON.parse(
    readFileSync(new URL(`../src/fixtures/${name}`, import.meta.url), "utf8"),
  );
}

const request = MvpShoppingRequestSchema.parse(fixture("request.json"));
const discoveries = DiscoveryResultSchema.array().parse(fixture("discovery.json"));
const offers = normalizeOffers(discoveries, request);
const plans = generatePlans(offers, request);
const carts = buildCartRequests(plans, request, offers);
const quotes = await Promise.all(carts.map((cart) => verifyCart(cart)));
const result = finalizeResults(offers, plans, quotes);

console.log(
  JSON.stringify(
    {
      offerCount: offers.length,
      planCount: plans.length,
      distinctCartCount: carts.length,
      winnerPlanId: result.winnerPlanId,
      lowestVerifiedTotalCents: result.lowestVerifiedTotalCents,
      bestSingleSupplierTotalCents: result.bestSingleSupplierTotalCents,
      savingsCents: result.savingsCents,
      scope: result.scope,
    },
    null,
    2,
  ),
);
