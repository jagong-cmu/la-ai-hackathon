// Plain-language summaries of a job for the owner's screens. No ids.
import type { JobView, Plan } from "@/api/client";
import { cleanName, int, itemShort, shortName } from "@/lib/format";

/** supplierId -> clean display name, for every store and the distributor in the request. */
export function supplierNames(job: JobView | null): Record<string, string> {
  if (!job) return {};
  const out: Record<string, string> = {};
  for (const s of job.request.suppliers) out[s.id] = cleanName(s.name);
  if (job.request.distributor) out[job.request.distributor.id] = cleanName(job.request.distributor.name);
  return out;
}

export function supplierUrls(job: JobView | null): Record<string, string> {
  return Object.fromEntries((job?.request.suppliers ?? []).map((s) => [s.id, s.url]));
}

/** itemId -> short lower-case noun, e.g. "nitrile gloves". */
export function itemNames(job: JobView | null): Record<string, string> {
  return Object.fromEntries((job?.request.items ?? []).map((i) => [i.id, itemShort(i.name).toLowerCase()]));
}

/** "Gloves + bread bags", "500 bakery boxes", "Gloves, bags + 2 more". */
export function orderTitle(job: JobView): string {
  const items = job.request.items.filter((i) => i.quantity > 0);
  if (items.length === 0) return "Empty order";
  if (items.length === 1) return `${int(items[0].quantity)} ${itemShort(items[0].name).toLowerCase()}`;
  const names = items.map((i) => itemShort(i.name).toLowerCase());
  const head = names.length === 2 ? `${names[0]} + ${names[1]}` : `${names[0]}, ${names[1]} + ${names.length - 2} more`;
  return head.charAt(0).toUpperCase() + head.slice(1);
}

/** The plan the owner chose, else the recommended plan. */
export function headlinePlan(job: JobView): Plan | null {
  const plans = job.results?.plans ?? job.plans ?? [];
  const id = job.approval?.action === "approve" ? job.approval.planId : job.results?.recommendedPlanId;
  return plans.find((p) => p.planId === id) ?? null;
}

/** "Gloves Alpha · bags Bravo" for a split, "All from Bravo" for one store. */
export function planShort(plan: Plan, job: JobView): string {
  const names = supplierNames(job);
  const items = itemNames(job);
  const sups = [...new Set(plan.assignments.map((a) => a.supplierId))];
  if (sups.length === 1) return `All from ${shortName(names[sups[0]] ?? sups[0])}`;
  return plan.assignments
    .map((a) => `${(items[a.itemId] ?? a.itemId).split(" ").slice(-1)[0]} ${shortName(names[a.supplierId] ?? a.supplierId)}`)
    .join(" · ");
}

const STATE_WORD: Record<string, string> = {
  created: "Starting",
  gathering: "Finding prices",
  results_ready: "Needs approval",
  approved: "Filling carts",
  handing_off: "Filling carts",
  at_payment: "At payment",
  partial_handoff: "Partly at payment",
  rejected: "Rejected",
  failed: "Failed",
};
export const jobStateWord = (s: string | null | undefined) => (s ? STATE_WORD[s] ?? s : "");

/** Where a job row opens: the screen for the step the job is at. */
export function jobHref(job: JobView): string {
  const base = `/jobs/${job.jobId}`;
  if (["approved", "handing_off", "at_payment", "partial_handoff"].includes(job.state)) return `${base}/checkout`;
  if (job.results) return `${base}/results`;
  return base;
}

const REASON: Record<string, string> = {
  bot_check: "Bot check",
  login_required: "Login required",
  login: "Login required",
  captcha: "Bot check",
  no_eligible_items: "No matching items",
  not_found: "Not found",
  timeout: "Timed out",
  "filtered: is supplier": "not a seller",
  not_a_seller: "not a seller",
  out_of_stock: "Out of stock",
};
export const reasonWord = (r: string | null | undefined) => (r ? REASON[r] ?? r.replaceAll("_", " ") : "");

/** Grid columns for the browser tiles (and the call tile): 2 x 2 up to 4, 3 x 3 up to 9 (8 stores + the call). */
export function tileCols(tiles: number): number {
  return tiles <= 4 ? 2 : tiles <= 9 ? 3 : 4;
}
