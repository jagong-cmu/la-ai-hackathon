import { z } from "zod";
import type {
  Allocation,
  CartQuote,
  CartRequest,
  CheckoutContact,
  ComparisonJob,
  ComparisonResult,
  DiscoveryResult,
  NormalizedOffer,
  ProductOffer,
  PurchasePlan,
  ShippingAddress,
  ShoppingItem,
  ShoppingRequest,
  Supplier,
} from "./types.js";

const IdSchema = z.string().trim().min(1);
const CentsSchema = z.number().int().nonnegative();
const NullableCentsSchema = CentsSchema.nullable();
const HttpUrlSchema = z.url().refine((value) => {
  const protocol = new URL(value).protocol;
  return protocol === "http:" || protocol === "https:";
}, "URL must use HTTP or HTTPS");

export const SupplierSchema: z.ZodType<Supplier> = z.object({
  id: IdSchema,
  name: z.string().trim().min(1),
  url: HttpUrlSchema,
});

export const ShoppingItemSchema: z.ZodType<ShoppingItem> = z.object({
  id: IdSchema,
  description: z.string().trim().min(1),
  quantity: z.number().int().positive(),
  unit: z.string().trim().min(1),
});

export const ShippingAddressSchema: z.ZodType<ShippingAddress> = z.object({
  line1: z.string().trim().min(1),
  city: z.string().trim().min(1),
  state: z.string().trim().length(2),
  postalCode: z.string().trim().regex(/^\d{5}(?:-\d{4})?$/),
  country: z.literal("US"),
});

export const CheckoutContactSchema: z.ZodType<CheckoutContact> = z.object({
  firstName: z.string().trim().min(1),
  lastName: z.string().trim().min(1),
  email: z.email(),
  phone: z.string().trim().regex(/^\+?[0-9() .-]{10,20}$/, "Invalid phone number"),
});

export const ShoppingRequestSchema: z.ZodType<ShoppingRequest> = z
  .object({
    items: z.array(ShoppingItemSchema).min(1),
    suppliers: z.array(SupplierSchema).min(1),
    address: ShippingAddressSchema,
    contact: CheckoutContactSchema,
  })
  .superRefine((request, context) => {
    const itemIds = new Set<string>();
    request.items.forEach((item, index) => {
      if (itemIds.has(item.id)) {
        context.addIssue({
          code: "custom",
          message: `Duplicate item ID: ${item.id}`,
          path: ["items", index, "id"],
        });
      }
      itemIds.add(item.id);
    });

    const supplierIds = new Set<string>();
    request.suppliers.forEach((supplier, index) => {
      if (supplierIds.has(supplier.id)) {
        context.addIssue({
          code: "custom",
          message: `Duplicate supplier ID: ${supplier.id}`,
          path: ["suppliers", index, "id"],
        });
      }
      supplierIds.add(supplier.id);
    });
  });

export const MvpShoppingRequestSchema = ShoppingRequestSchema.refine(
  (request) => request.items.length === 2,
  { message: "The MVP requires exactly two items", path: ["items"] },
).refine((request) => request.suppliers.length === 2, {
  message: "The MVP requires exactly two suppliers",
  path: ["suppliers"],
});

const ProductOfferObjectSchema = z.object({
  id: IdSchema,
  supplierId: IdSchema,
  itemId: IdSchema,
  title: z.string().trim().min(1),
  sku: z.string().trim().min(1).nullable(),
  productUrl: HttpUrlSchema,
  unitsPerPack: z.number().int().positive(),
  packUnit: z.string().trim().min(1),
  packPriceCents: CentsSchema,
  inStock: z.boolean(),
  matchStatus: z.enum(["exact", "uncertain"]),
});

export const ProductOfferSchema: z.ZodType<ProductOffer> = ProductOfferObjectSchema;

export const DiscoveryResultSchema: z.ZodType<DiscoveryResult> = z.object({
  supplierId: IdSchema,
  offers: z.array(ProductOfferSchema),
  missingItemIds: z.array(IdSchema),
  errors: z.array(z.string()),
  runId: z.string().trim().min(1).nullable(),
});

export const NormalizedOfferSchema: z.ZodType<NormalizedOffer> = ProductOfferObjectSchema.extend({
  packsNeeded: z.number().int().positive(),
  merchandiseCostCents: CentsSchema,
});

export const AllocationSchema: z.ZodType<Allocation> = z.object({
  itemId: IdSchema,
  supplierId: IdSchema,
  offerId: IdSchema,
  packs: z.number().int().positive(),
});

export const PurchasePlanSchema: z.ZodType<PurchasePlan> = z.object({
  id: IdSchema,
  allocations: z.array(AllocationSchema).min(1),
  merchandiseSubtotalCents: CentsSchema,
  quoteKeys: z.array(IdSchema),
  totalCents: NullableCentsSchema,
  status: z.enum(["estimated", "verified", "incomplete"]),
  issues: z.array(z.string()),
});

const CartRequestItemSchema = z.object({
  itemId: IdSchema,
  offerId: IdSchema,
  title: z.string().trim().min(1),
  sku: z.string().trim().min(1).nullable(),
  productUrl: HttpUrlSchema,
  packs: z.number().int().positive(),
  unitsPerPack: z.number().int().positive(),
});

export const CartRequestSchema: z.ZodType<CartRequest> = z.object({
  key: IdSchema,
  supplier: SupplierSchema,
  items: z.array(CartRequestItemSchema).min(1),
  address: ShippingAddressSchema,
  contact: CheckoutContactSchema,
});

const CartQuoteLineSchema = z.object({
  offerId: IdSchema,
  observedTitle: z.string().trim().min(1),
  observedSku: z.string().trim().min(1).nullable(),
  packs: z.number().int().positive(),
  lineTotalCents: CentsSchema,
});

export const CartQuoteSchema: z.ZodType<CartQuote> = z.object({
  key: IdSchema,
  supplierId: IdSchema,
  lines: z.array(CartQuoteLineSchema),
  costs: z.object({
    subtotalCents: NullableCentsSchema,
    shippingCents: NullableCentsSchema,
    taxCents: NullableCentsSchema,
    feesCents: NullableCentsSchema,
    discountsCents: NullableCentsSchema,
    totalCents: NullableCentsSchema,
  }),
  status: z.enum(["verified", "incomplete", "failed"]),
  evidence: z.object({
    runId: z.string().trim().min(1).nullable(),
    checkoutUrl: HttpUrlSchema.nullable(),
    screenshotUrl: HttpUrlSchema.nullable(),
  }),
  error: z.string().nullable(),
});

export const ComparisonResultSchema: z.ZodType<ComparisonResult> = z.object({
  offers: z.array(NormalizedOfferSchema),
  plans: z.array(PurchasePlanSchema),
  quotes: z.array(CartQuoteSchema),
  winnerPlanId: IdSchema.nullable(),
  lowestVerifiedTotalCents: NullableCentsSchema,
  bestSingleSupplierPlanId: IdSchema.nullable(),
  bestSingleSupplierTotalCents: NullableCentsSchema,
  savingsCents: NullableCentsSchema,
  scope: z.enum(["all_feasible_verified", "partial_verified", "none_verified"]),
  limitations: z.array(z.string()),
});

export const ComparisonJobSchema: z.ZodType<ComparisonJob> = z.object({
  id: IdSchema,
  status: z.enum([
    "queued",
    "discovering",
    "optimizing",
    "verifying",
    "cancelling",
    "cancelled",
    "complete",
    "failed",
  ]),
  mode: z.enum(["fixture", "live"]),
  message: z.string(),
  progress: z.object({
    completed: z.number().int().nonnegative(),
    total: z.number().int().nonnegative(),
  }),
  browserViews: z.array(
    z.object({
      runId: z.string().trim().min(1),
      label: z.string().trim().min(1),
      liveUrl: HttpUrlSchema.nullable(),
      status: z.enum([
        "queued",
        "running",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
      ]),
    }),
  ),
  result: ComparisonResultSchema.nullable(),
  error: z.string().nullable(),
});
