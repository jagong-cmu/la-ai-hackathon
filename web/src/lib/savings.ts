// Savings against the QuickBooks bills. The past price of an item is the quantity-weighted mean
// of its bill lines (backend app/data/insights.py past_unit_price, the same basis as
// JobResults.savingsVsPaidBefore). Code does every number here; no model output.
import type { BillLine, JobView, Plan, ShoppingItem } from "@/api/client";
import { headlinePlan, supplierNames } from "@/lib/job";

export interface SavingsRow {
  item: ShoppingItem;
  supplierId: string;
  source: string;
  /** What this quantity cost at the past bill price. */
  paidBefore: number;
  /** The plan's line cost for this item (before shipping and tax). */
  now: number;
  unitBefore: number;
  unitNow: number;
}

export interface PlanSavings {
  rows: SavingsRow[];
  /** Shipping and tax of the plan's carts. They count against the saving. */
  extras: number;
  paidBefore: number;
  now: number;
  saved: number;
  /** saved / paidBefore, 0..1 (negative when the plan costs more). */
  pct: number;
  /** Items in the order that the comparison leaves out (no past price or not in the plan). */
  skipped: number;
}

/** The plan against the bill prices, item by item. Null when no item has a past price. */
export function planSavings(job: JobView, plan: Plan | null | undefined): PlanSavings | null {
  if (!plan) return null;
  const items = job.request.items.filter((i) => i.quantity > 0);
  const rows: SavingsRow[] = [];
  for (const item of items) {
    const a = plan.assignments.find((x) => x.itemId === item.id);
    if (!a || item.pastUnitPrice == null) continue;
    rows.push({
      item,
      supplierId: a.supplierId,
      source: a.source,
      paidBefore: item.quantity * item.pastUnitPrice,
      now: a.lineCost,
      unitBefore: item.pastUnitPrice,
      unitNow: a.lineCost / item.quantity,
    });
  }
  if (rows.length === 0) return null;
  const covered = new Set(rows.map((r) => r.supplierId));
  const extras = plan.carts.filter((c) => covered.has(c.supplierId)).reduce((n, c) => n + c.shipping + c.tax, 0);
  const paidBefore = rows.reduce((n, r) => n + r.paidBefore, 0);
  const now = rows.reduce((n, r) => n + r.now, 0) + extras;
  return { rows, extras, paidBefore, now, saved: paidBefore - now, pct: paidBefore > 0 ? (paidBefore - now) / paidBefore : 0, skipped: items.length - rows.length };
}

/** The plan an order's price stands on: the chosen or recommended plan, else the quote, else the cheapest. */
export function pricedPlan(job: JobView): Plan | null {
  const plans = job.results?.plans ?? job.plans ?? [];
  return (
    headlinePlan(job) ??
    plans.find((p) => p.planId === job.results?.quotedPlanId) ??
    [...plans].sort((a, b) => a.total - b.total)[0] ??
    null
  );
}

/** Savings over many orders: the chosen plan of approved orders, the best plan of every priced one. */
export function savingsTotals(jobs: JobView[]) {
  let approved = 0;
  let approvedCount = 0;
  let found = 0;
  let foundCount = 0;
  let paidBefore = 0;
  for (const j of jobs) {
    if (!j.results || j.approval?.action === "reject") continue;
    const s = planSavings(j, pricedPlan(j));
    if (!s) continue;
    found += s.saved;
    foundCount += 1;
    paidBefore += s.paidBefore;
    if (j.approval?.action === "approve") {
      approved += s.saved;
      approvedCount += 1;
    }
  }
  return { approved, approvedCount, found, foundCount, pct: paidBefore > 0 ? found / paidBefore : 0 };
}

export interface ItemQuote {
  jobId: string;
  at: string;
  supplierId: string;
  supplier: string;
  source: string;
  status: string;
  quantity: number;
  lineCost: number;
  unit: number;
  /** The quote is the one the order's plan uses for this item. */
  used: boolean;
}

/** Every price found for one item across the orders, cheapest per store and order. */
export function itemQuotes(jobs: JobView[], itemId: string): ItemQuote[] {
  const out: ItemQuote[] = [];
  for (const j of jobs) {
    const item = j.request.items.find((i) => i.id === itemId && i.quantity > 0);
    const plans = j.results?.plans ?? j.plans ?? [];
    if (!item || plans.length === 0) continue;
    const names = supplierNames(j);
    const chosen = headlinePlan(j)?.assignments.find((a) => a.itemId === itemId);
    const best = new Map<string, ItemQuote>();
    for (const p of plans) {
      for (const a of p.assignments) {
        if (a.itemId !== itemId) continue;
        const cart = p.carts.find((c) => c.supplierId === a.supplierId);
        const q: ItemQuote = {
          jobId: j.jobId,
          at: j.createdAt ?? "",
          supplierId: a.supplierId,
          supplier: names[a.supplierId] ?? a.supplierId,
          source: a.source,
          status: a.source === "web" ? cart?.status ?? p.status : "quoted",
          quantity: item.quantity,
          lineCost: a.lineCost,
          unit: a.lineCost / item.quantity,
          used: chosen?.supplierId === a.supplierId && chosen.sku === a.sku,
        };
        const prev = best.get(a.supplierId);
        if (!prev || q.unit < prev.unit || (q.used && q.unit <= prev.unit)) best.set(a.supplierId, q);
      }
    }
    out.push(...[...best.values()].sort((a, b) => a.unit - b.unit));
  }
  return out.sort((a, b) => b.at.localeCompare(a.at) || a.unit - b.unit);
}

/** The lowest price found per unit, or null. */
export function bestQuote(quotes: ItemQuote[]): ItemQuote | null {
  return quotes.reduce<ItemQuote | null>((b, q) => (!b || q.unit < b.unit ? q : b), null);
}

/** Quantity-weighted mean unit price of bill lines. */
export function meanPaid(lines: BillLine[]): number | null {
  const qty = lines.reduce((n, l) => n + l.quantity, 0);
  return qty > 0 ? lines.reduce((n, l) => n + l.amount, 0) / qty : null;
}

/** "−12%" style share, rounded to a whole percent. */
export const pct = (x: number) => `${Math.round(Math.abs(x) * 100)}%`;
