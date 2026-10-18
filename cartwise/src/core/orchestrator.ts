import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { verifySupplierCarts } from "../browser/checkout.js";
import { cancelBrowserRun, type BrowserTaskObserver } from "../browser/client.js";
import { discoverProducts } from "../browser/discovery.js";
import { config } from "../config.js";
import { getJob, updateJob } from "../jobs/store.js";
import { ComparisonResultSchema } from "../schemas.js";
import type { BrowserView, CartQuote, DiscoveryResult, ShoppingRequest } from "../types.js";
import { buildCartRequests } from "./cart-requests.js";
import { normalizeOffers } from "./normalize.js";
import { generatePlans } from "./optimizer.js";
import { finalizeResults } from "./results.js";

const cancelledJobs = new Set<string>();
const activeRunsByJob = new Map<string, Set<string>>();

class ComparisonCancelledError extends Error {
  constructor() {
    super("Comparison cancelled");
    this.name = "ComparisonCancelledError";
  }
}

function throwIfCancelled(jobId: string): void {
  if (cancelledJobs.has(jobId) || getJob(jobId)?.status === "cancelled") {
    throw new ComparisonCancelledError();
  }
}

export async function cancelComparison(jobId: string) {
  const job = getJob(jobId);
  if (!job) return null;
  if (["complete", "failed", "cancelled"].includes(job.status)) return job;

  cancelledJobs.add(jobId);
  updateJob(jobId, {
    status: "cancelling",
    message: "Stopping active agents",
    error: null,
    browserViews: job.browserViews.map((view) =>
      view.status === "queued"
        ? { ...view, status: "cancelled" as const }
        : view.status === "running"
          ? { ...view, status: "cancelling" as const }
          : view,
    ),
  });

  const activeRuns = [...(activeRunsByJob.get(jobId) ?? [])];
  const results = await Promise.allSettled(activeRuns.map((runId) => cancelBrowserRun(runId)));
  const failures = results.flatMap((result, index) =>
    result.status === "rejected"
      ? [{ runId: activeRuns[index]!, reason: result.reason }]
      : [],
  );

  if (failures.length > 0) {
    const message = failures
      .map(({ runId, reason }) =>
        `${runId}: ${reason instanceof Error ? reason.message : String(reason)}`,
      )
      .join("; ");
    console.warn(`[browser-use] cancellation incomplete: ${message}`);
    updateJob(jobId, {
      status: "failed",
      message: "Could not stop all active agents",
      error: message,
      browserViews: getJob(jobId)!.browserViews.map((view) =>
        view.status === "cancelling" ? { ...view, status: "failed" as const } : view,
      ),
    });
    return getJob(jobId);
  }

  updateJob(jobId, {
    status: "cancelled",
    message: "Comparison cancelled",
    error: null,
    browserViews: getJob(jobId)!.browserViews.map((view) =>
      ["queued", "running", "cancelling"].includes(view.status)
        ? { ...view, status: "cancelled" as const }
        : view,
    ),
  });
  return getJob(jobId);
}

async function persistResult(jobId: string, result: unknown): Promise<void> {
  const runsDirectory = fileURLToPath(new URL("../../runs", import.meta.url));
  await mkdir(runsDirectory, { recursive: true });
  await writeFile(
    fileURLToPath(new URL(`../../runs/${jobId}.json`, import.meta.url)),
    `${JSON.stringify(result, null, 2)}\n`,
    { mode: 0o600 },
  );
}

function queuedView(jobId: string, phase: string, id: string, label: string): BrowserView {
  return {
    runId: `queued:${jobId}:${phase}:${id}`,
    label,
    liveUrl: null,
    status: "queued",
  };
}

function browserObserver(
  jobId: string,
  label: string,
  placeholderId: string,
): BrowserTaskObserver {
  const finishedRuns = new Map<string, "completed" | "failed" | "cancelled">();
  return {
    label,
    isCancelled: () => cancelledJobs.has(jobId) || getJob(jobId)?.status === "cancelled",
    onStarted: (runId) => {
      const activeRuns = activeRunsByJob.get(jobId) ?? new Set<string>();
      activeRuns.add(runId);
      activeRunsByJob.set(jobId, activeRuns);
      if (cancelledJobs.has(jobId)) void cancelBrowserRun(runId);
      const job = getJob(jobId);
      if (!job) return;
      const browserViews = job.browserViews.filter(
        (candidate) => candidate.runId !== placeholderId && candidate.runId !== runId,
      );
      browserViews.push({ runId, label, liveUrl: null, status: "running" });
      updateJob(jobId, { browserViews });
    },
    onBrowserReady: (view) => {
      const job = getJob(jobId);
      if (!job) return;
      const replacement = {
        ...view,
        status:
          finishedRuns.get(view.runId) ??
          (job.status === "cancelled" ? "cancelled" as const : "running" as const),
      };
      let found = false;
      const browserViews = job.browserViews.map((candidate) => {
        if (candidate.runId !== view.runId) return candidate;
        found = true;
        return replacement;
      });
      if (!found) browserViews.push(replacement);
      updateJob(jobId, { browserViews });
    },
    onFinished: (runId, status) => {
      const activeRuns = activeRunsByJob.get(jobId);
      activeRuns?.delete(runId);
      const job = getJob(jobId);
      if (!job) return;
    const finalStatus = ["cancelling", "cancelled"].includes(job.status)
      ? "cancelled"
      : status;
      finishedRuns.set(runId, finalStatus);
      updateJob(jobId, {
        browserViews: job.browserViews.map((view) =>
          view.runId === runId ? { ...view, status: finalStatus } : view,
        ),
      });
    },
  };
}

export async function runComparison(request: ShoppingRequest, jobId: string): Promise<void> {
  try {
    throwIfCancelled(jobId);
    const discoveryViews = request.suppliers.map((supplier) =>
      queuedView(
        jobId,
        "discovery",
        supplier.id,
        `Finding products at ${supplier.name}`,
      ),
    );
    updateJob(jobId, {
      status: "discovering",
      message: `Finding products at ${request.suppliers.length} suppliers`,
      progress: { completed: 0, total: request.suppliers.length },
      browserViews: config.mode === "live" ? discoveryViews : [],
    });
    let completedDiscoveries = 0;
    const discoveries = await Promise.all(
      request.suppliers.map(async (supplier, index): Promise<DiscoveryResult> => {
        let discovery: DiscoveryResult;
        try {
          discovery = await discoverProducts(
            supplier,
            request.items,
            browserObserver(
              jobId,
              `Finding products at ${supplier.name}`,
              discoveryViews[index]!.runId,
            ),
          );
        } catch (error) {
          discovery = {
            supplierId: supplier.id,
            offers: [],
            missingItemIds: request.items.map((item) => item.id),
            errors: [error instanceof Error ? error.message : String(error)],
            runId: null,
          };
        }
        completedDiscoveries += 1;
        const job = getJob(jobId);
        if (job?.status === "discovering") {
          updateJob(jobId, {
            progress: {
              completed: completedDiscoveries,
              total: request.suppliers.length,
            },
          });
        }
        return discovery;
      }),
    );

    throwIfCancelled(jobId);

    const offers = normalizeOffers(discoveries, request);
    updateJob(jobId, { status: "optimizing", message: "Building purchase plans" });
    const plans = generatePlans(offers, request);
    const carts = buildCartRequests(plans, request, offers);
    updateJob(jobId, {
      status: "verifying",
      message: carts.length ? "Checking supplier carts" : "No feasible carts to verify",
      progress: { completed: 0, total: carts.length },
    });

    const cartsBySupplier = new Map<string, typeof carts>();
    for (const cart of carts) {
      const supplierCarts = cartsBySupplier.get(cart.supplier.id) ?? [];
      supplierCarts.push(cart);
      cartsBySupplier.set(cart.supplier.id, supplierCarts);
    }

    const supplierBatches = [...cartsBySupplier.values()];
    const checkoutViews = supplierBatches.map((supplierCarts) => {
      const supplier = supplierCarts[0]!.supplier;
      return queuedView(
        jobId,
        "checkout",
        supplier.id,
        `Checking ${supplierCarts.length} carts at ${supplier.name}`,
      );
    });
    if (config.mode === "live" && checkoutViews.length > 0) {
      const job = getJob(jobId);
      if (job) updateJob(jobId, { browserViews: [...job.browserViews, ...checkoutViews] });
    }

    const quotes: CartQuote[] = [];
    let completedCarts = 0;
    await Promise.all(
      supplierBatches.map(async (supplierCarts, index) => {
        const supplier = supplierCarts[0]!.supplier;
        const supplierQuotes = await verifySupplierCarts(
          supplierCarts,
          browserObserver(
            jobId,
            `Checking ${supplierCarts.length} carts at ${supplier.name}`,
            checkoutViews[index]!.runId,
          ),
        );
        quotes.push(...supplierQuotes);
        completedCarts += supplierCarts.length;
        updateJob(jobId, {
          progress: {
            completed: Math.min(completedCarts, carts.length),
            total: carts.length,
          },
        });
      }),
    );

    throwIfCancelled(jobId);

    quotes.sort((left, right) => left.key.localeCompare(right.key));
    const result = ComparisonResultSchema.parse(finalizeResults(offers, plans, quotes));
    await persistResult(jobId, result);
    updateJob(jobId, {
      status: "complete",
      message: plans.length ? "Comparison complete" : "No feasible purchase plan found",
      progress: { completed: carts.length, total: carts.length },
      result,
      error: null,
    });
  } catch (error) {
    if (
      error instanceof ComparisonCancelledError ||
      ["cancelling", "cancelled"].includes(getJob(jobId)?.status ?? "")
    ) {
      return;
    }
    updateJob(jobId, {
      status: "failed",
      message: "Comparison failed",
      error: error instanceof Error ? error.message : String(error),
    });
  } finally {
    activeRunsByJob.delete(jobId);
    cancelledJobs.delete(jobId);
  }
}
