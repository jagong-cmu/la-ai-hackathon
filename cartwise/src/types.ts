export type Supplier = {
  id: string;
  name: string;
  url: string;
};

export type ShoppingItem = {
  id: string;
  description: string;
  quantity: number;
  unit: string;
};

export type ShippingAddress = {
  line1: string;
  city: string;
  state: string;
  postalCode: string;
  country: "US";
};

export type CheckoutContact = {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
};

export type ShoppingRequest = {
  items: ShoppingItem[];
  suppliers: Supplier[];
  address: ShippingAddress;
  contact: CheckoutContact;
};

export type ProductOffer = {
  id: string;
  supplierId: string;
  itemId: string;
  title: string;
  sku: string | null;
  productUrl: string;
  unitsPerPack: number;
  packUnit: string;
  packPriceCents: number;
  inStock: boolean;
  matchStatus: "exact" | "uncertain";
};

export type DiscoveryResult = {
  supplierId: string;
  offers: ProductOffer[];
  missingItemIds: string[];
  errors: string[];
  runId: string | null;
};

export type NormalizedOffer = ProductOffer & {
  packsNeeded: number;
  merchandiseCostCents: number;
};

export type Allocation = {
  itemId: string;
  supplierId: string;
  offerId: string;
  packs: number;
};

export type PurchasePlan = {
  id: string;
  allocations: Allocation[];
  merchandiseSubtotalCents: number;
  quoteKeys: string[];
  totalCents: number | null;
  status: "estimated" | "verified" | "incomplete";
  issues: string[];
};

export type CartRequest = {
  key: string;
  supplier: Supplier;
  items: {
    itemId: string;
    offerId: string;
    title: string;
    sku: string | null;
    productUrl: string;
    packs: number;
    unitsPerPack: number;
  }[];
  address: ShippingAddress;
  contact: CheckoutContact;
};

export type CartQuote = {
  key: string;
  supplierId: string;
  lines: {
    offerId: string;
    observedTitle: string;
    observedSku: string | null;
    packs: number;
    lineTotalCents: number;
  }[];
  costs: {
    subtotalCents: number | null;
    shippingCents: number | null;
    taxCents: number | null;
    feesCents: number | null;
    discountsCents: number | null;
    totalCents: number | null;
  };
  status: "verified" | "incomplete" | "failed";
  evidence: {
    runId: string | null;
    checkoutUrl: string | null;
    screenshotUrl: string | null;
  };
  error: string | null;
};

export type ComparisonResult = {
  offers: NormalizedOffer[];
  plans: PurchasePlan[];
  quotes: CartQuote[];
  winnerPlanId: string | null;
  lowestVerifiedTotalCents: number | null;
  bestSingleSupplierPlanId: string | null;
  bestSingleSupplierTotalCents: number | null;
  savingsCents: number | null;
  scope: "all_feasible_verified" | "partial_verified" | "none_verified";
  limitations: string[];
};

export type JobStatus =
  | "queued"
  | "discovering"
  | "optimizing"
  | "verifying"
  | "cancelling"
  | "cancelled"
  | "complete"
  | "failed";

export type BrowserView = {
  runId: string;
  label: string;
  liveUrl: string | null;
  status: "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
};

export type ComparisonJob = {
  id: string;
  status: JobStatus;
  mode: "fixture" | "live";
  message: string;
  progress: {
    completed: number;
    total: number;
  };
  browserViews: BrowserView[];
  result: ComparisonResult | null;
  error: string | null;
};
