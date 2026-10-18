import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { z } from "zod";
import { config } from "../config.js";
import { DiscoveryResultSchema } from "../schemas.js";
import type { DiscoveryResult, ProductOffer, ShoppingItem, Supplier } from "../types.js";
import { runBrowserTask, type BrowserTaskObserver } from "./client.js";
import { buildDiscoveryPrompt } from "./prompts.js";

const ObservedOfferSchema = z.object({
  itemId: z.string().trim().min(1),
  title: z.string().trim().min(1),
  sku: z.string().trim().min(1).nullable(),
  productUrl: z.url(),
  unitsPerPack: z.number().int().positive(),
  packUnit: z.string().trim().min(1),
  packPriceCents: z.number().int().nonnegative(),
  inStock: z.boolean(),
  matchStatus: z.enum(["exact", "uncertain"]),
});

export const DiscoveryAgentOutputSchema = z.object({
  offers: z.array(ObservedOfferSchema),
  missingItemIds: z.array(z.string().trim().min(1)),
  errors: z.array(z.string()),
});

export type DiscoveryAgentOutput = z.infer<typeof DiscoveryAgentOutputSchema>;

function stableOfferId(supplierId: string, offer: z.infer<typeof ObservedOfferSchema>): string {
  const identity = offer.sku?.toLowerCase() ?? new URL(offer.productUrl).toString();
  const digest = createHash("sha256")
    .update(`${supplierId}\n${offer.itemId}\n${identity}`)
    .digest("hex")
    .slice(0, 20);

  return `offer-${digest}`;
}

function belongsToSupplier(productUrl: string, supplierUrl: string): boolean {
  const product = new URL(productUrl);
  const supplier = new URL(supplierUrl);

  if (!['http:', 'https:'].includes(product.protocol)) {
    return false;
  }

  return (
    product.hostname === supplier.hostname ||
    product.hostname.endsWith(`.${supplier.hostname}`) ||
    supplier.hostname.endsWith(`.${product.hostname}`)
  );
}

function fulfillmentRank(offer: ProductOffer, item: ShoppingItem): [number, number, string] {
  const packs = Math.ceil(item.quantity / offer.unitsPerPack);
  const cost = packs * offer.packPriceCents;
  const excessUnits = packs * offer.unitsPerPack - item.quantity;
  return [cost, excessUnits, offer.id];
}

function compareRank(left: [number, number, string], right: [number, number, string]): number {
  return left[0] - right[0] || left[1] - right[1] || left[2].localeCompare(right[2]);
}

export function buildDiscoveryResult(
  supplier: Supplier,
  items: ShoppingItem[],
  output: DiscoveryAgentOutput,
  runId: string | null,
): DiscoveryResult {
  const requestedItems = new Map(items.map((item) => [item.id, item]));
  const candidates = new Map<string, ProductOffer[]>();
  const errors = [...output.errors];

  for (const observed of output.offers) {
    const item = requestedItems.get(observed.itemId);
    if (!item) {
      errors.push(`Rejected offer with unknown itemId: ${observed.itemId}`);
      continue;
    }

    if (!belongsToSupplier(observed.productUrl, supplier.url)) {
      errors.push(`Rejected off-domain product URL for item ${observed.itemId}`);
      continue;
    }

    const offer: ProductOffer = {
      ...observed,
      id: stableOfferId(supplier.id, observed),
      supplierId: supplier.id,
    };

    const existing = candidates.get(item.id) ?? [];
    if (!existing.some((candidate) => candidate.id === offer.id)) {
      existing.push(offer);
      candidates.set(item.id, existing);
    }
  }

  const selectedOffers: ProductOffer[] = [];
  const missingItemIds = new Set(
    output.missingItemIds.filter((itemId) => {
      if (!requestedItems.has(itemId)) {
        errors.push(`Ignored unknown missing itemId: ${itemId}`);
        return false;
      }
      return true;
    }),
  );

  for (const item of items) {
    const itemCandidates = candidates.get(item.id) ?? [];
    const eligible = itemCandidates.filter(
      (offer) => offer.inStock && offer.matchStatus === "exact" && offer.packUnit === item.unit,
    );

    eligible.sort((left, right) =>
      compareRank(fulfillmentRank(left, item), fulfillmentRank(right, item)),
    );

    if (eligible[0]) {
      selectedOffers.push(eligible[0]);
      missingItemIds.delete(item.id);
      continue;
    }

    const fallback = itemCandidates.sort((left, right) => left.id.localeCompare(right.id))[0];
    if (fallback) {
      selectedOffers.push(fallback);
    }
    missingItemIds.add(item.id);
  }

  return DiscoveryResultSchema.parse({
    supplierId: supplier.id,
    offers: selectedOffers,
    missingItemIds: [...missingItemIds].sort(),
    errors,
    runId,
  });
}

function readFixtureDiscoveries(): DiscoveryResult[] {
  const contents = readFileSync(new URL("../fixtures/discovery.json", import.meta.url), "utf8");
  return DiscoveryResultSchema.array().parse(JSON.parse(contents));
}

export async function discoverProducts(
  supplier: Supplier,
  items: ShoppingItem[],
  observer?: BrowserTaskObserver,
): Promise<DiscoveryResult> {
  if (config.mode === "fixture") {
    const fixture = readFixtureDiscoveries().find((result) => result.supplierId === supplier.id);
    if (!fixture) {
      return {
        supplierId: supplier.id,
        offers: [],
        missingItemIds: items.map((item) => item.id),
        errors: [`No discovery fixture exists for supplier ${supplier.id}`],
        runId: null,
      };
    }
    return fixture;
  }

  const result = await runBrowserTask(
    buildDiscoveryPrompt(supplier, items),
    DiscoveryAgentOutputSchema,
    { retryTransient: true, observer },
  );

  return buildDiscoveryResult(supplier, items, result.data, result.runId);
}
