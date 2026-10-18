import { ComparisonResultSchema } from "../schemas.js";
import type { CartQuote, ComparisonResult, NormalizedOffer, PurchasePlan } from "../types.js";

function supplierCount(plan: PurchasePlan): number {
  return new Set(plan.allocations.map((allocation) => allocation.supplierId)).size;
}

function comparePlans(left: PurchasePlan, right: PurchasePlan): number {
  return (
    (left.totalCents ?? Number.POSITIVE_INFINITY) -
      (right.totalCents ?? Number.POSITIVE_INFINITY) ||
    supplierCount(left) - supplierCount(right) ||
    left.id.localeCompare(right.id)
  );
}

export function finalizeResults(
  offers: NormalizedOffer[],
  plans: PurchasePlan[],
  quotes: CartQuote[],
): ComparisonResult {
  const quotesByKey = new Map(quotes.map((quote) => [quote.key, quote]));
  const evaluatedPlans = plans.map<PurchasePlan>((plan) => {
    const issues = [...plan.issues];
    const planQuotes: CartQuote[] = [];

    for (const key of plan.quoteKeys) {
      const quote = quotesByKey.get(key);
      if (!quote) {
        issues.push(`Missing quote for cart ${key}`);
      } else if (quote.status !== "verified" || quote.costs.totalCents === null) {
        issues.push(`Cart ${key} is ${quote.status}${quote.error ? `: ${quote.error}` : ""}`);
      } else {
        planQuotes.push(quote);
      }
    }

    if (plan.quoteKeys.length === 0) {
      issues.push("Plan has no cart quotes");
    }

    if (issues.length > 0 || planQuotes.length !== plan.quoteKeys.length) {
      return { ...plan, status: "incomplete", totalCents: null, issues };
    }

    return {
      ...plan,
      status: "verified",
      totalCents: planQuotes.reduce((total, quote) => total + quote.costs.totalCents!, 0),
      issues: [],
    };
  });

  const verifiedPlans = evaluatedPlans
    .filter((plan) => plan.status === "verified" && plan.totalCents !== null)
    .sort(comparePlans);
  const winner = verifiedPlans[0] ?? null;
  const bestSingleSupplier = verifiedPlans
    .filter((plan) => supplierCount(plan) === 1)
    .sort(comparePlans)[0] ?? null;

  const scope =
    verifiedPlans.length === 0
      ? "none_verified"
      : verifiedPlans.length === evaluatedPlans.length
        ? "all_feasible_verified"
        : "partial_verified";

  const limitations = evaluatedPlans
    .filter((plan) => plan.status === "incomplete")
    .map((plan) => `Plan ${plan.id} was incomplete: ${plan.issues.join("; ")}`);
  if (verifiedPlans.length === 0) {
    limitations.push("No purchase plan had complete verified checkout quotes.");
  }

  return ComparisonResultSchema.parse({
    offers,
    plans: evaluatedPlans,
    quotes,
    winnerPlanId: winner?.id ?? null,
    lowestVerifiedTotalCents: winner?.totalCents ?? null,
    bestSingleSupplierPlanId: bestSingleSupplier?.id ?? null,
    bestSingleSupplierTotalCents: bestSingleSupplier?.totalCents ?? null,
    savingsCents:
      winner?.totalCents !== null &&
      winner?.totalCents !== undefined &&
      bestSingleSupplier?.totalCents !== null &&
      bestSingleSupplier?.totalCents !== undefined
        ? bestSingleSupplier.totalCents - winner.totalCents
        : null,
    scope,
    limitations,
  });
}
