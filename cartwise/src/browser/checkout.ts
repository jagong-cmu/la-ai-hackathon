import { readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import { config } from "../config.js";
import { CartQuoteSchema } from "../schemas.js";
import type { CartQuote, CartRequest } from "../types.js";
import { validateQuote } from "../core/validate-quote.js";
import {
  BrowserRunError,
  downloadWorkspaceFilesByBasename,
  runBrowserTask,
  type BrowserTaskObserver,
} from "./client.js";
import { buildBatchCheckoutPrompt, buildCheckoutPrompt } from "./prompts.js";

const NullableCentsSchema = z.number().int().nonnegative().nullable();

export const CheckoutAgentOutputSchema = z.object({
  lines: z.array(
    z.object({
      offerId: z.string().trim().min(1),
      observedTitle: z.string().trim().min(1),
      observedSku: z.string().trim().min(1).nullable(),
      packs: z.number().int().positive(),
      lineTotalCents: z.number().int().nonnegative(),
    }),
  ),
  costs: z.object({
    subtotalCents: NullableCentsSchema,
    shippingCents: NullableCentsSchema,
    taxCents: NullableCentsSchema,
    feesCents: NullableCentsSchema,
    discountsCents: NullableCentsSchema,
    totalCents: NullableCentsSchema,
  }),
  confirmedAbsentCostFields: z.array(z.enum(["feesCents", "discountsCents"])),
  reachedCompleteTotal: z.boolean(),
  checkoutUrl: z.url().nullable(),
  screenshotUrl: z.url().nullable(),
  status: z.enum(["complete", "incomplete", "failed"]),
  error: z.string().nullable(),
});

export type CheckoutAgentOutput = z.infer<typeof CheckoutAgentOutputSchema>;

export const CheckoutBatchAgentOutputSchema = z.object({
  quotes: z.array(
    CheckoutAgentOutputSchema.extend({
      cartKey: z.string().trim().min(1),
    }),
  ),
});

function emptyCosts(): CartQuote["costs"] {
  return {
    subtotalCents: null,
    shippingCents: null,
    taxCents: null,
    feesCents: null,
    discountsCents: null,
    totalCents: null,
  };
}

export function buildCartQuote(
  request: CartRequest,
  output: CheckoutAgentOutput,
  runId: string | null,
): CartQuote {
  const reportedComplete = output.status === "complete" && output.reachedCompleteTotal;
  const confirmedAbsent = new Set(output.confirmedAbsentCostFields);
  const costs = {
    ...output.costs,
    feesCents:
      output.costs.feesCents === null && confirmedAbsent.has("feesCents")
        ? 0
        : output.costs.feesCents,
    discountsCents:
      output.costs.discountsCents === null && confirmedAbsent.has("discountsCents")
        ? 0
        : output.costs.discountsCents,
  };
  const quote = CartQuoteSchema.parse({
    key: request.key,
    supplierId: request.supplier.id,
    lines: output.lines,
    costs,
    status: output.status === "failed" ? "failed" : reportedComplete ? "verified" : "incomplete",
    evidence: {
      runId,
      checkoutUrl: output.checkoutUrl,
      screenshotUrl: output.screenshotUrl,
    },
    error:
      output.error ??
      (reportedComplete ? null : "Browser agent did not reach a complete checkout total"),
  });

  return validateQuote(request, quote);
}

function failedQuote(request: CartRequest, error: unknown): CartQuote {
  const message = error instanceof Error ? error.message : String(error);
  return CartQuoteSchema.parse({
    key: request.key,
    supplierId: request.supplier.id,
    lines: [],
    costs: emptyCosts(),
    status: "failed",
    evidence: {
      runId: error instanceof BrowserRunError ? error.runId : null,
      checkoutUrl: null,
      screenshotUrl: null,
    },
    error: message,
  });
}

function fixtureQuote(request: CartRequest): CartQuote {
  const contents = readFileSync(new URL("../fixtures/quotes.json", import.meta.url), "utf8");
  const quote = CartQuoteSchema.array().parse(JSON.parse(contents)).find(
    (candidate) => candidate.key === request.key,
  );
  if (!quote) {
    return failedQuote(request, `No checkout fixture exists for cart ${request.key}`);
  }
  return validateQuote(request, quote);
}

async function persistCheckoutEvidence(
  requests: CartRequest[],
  runId: string,
  workspaceId: string | null,
): Promise<Map<string, string>> {
  const urls = new Map<string, string>();
  if (!workspaceId || !/^[a-zA-Z0-9-]{1,128}$/.test(runId)) return urls;

  const eligible = requests.filter((request) => /^cart-[a-f0-9]{64}$/.test(request.key));
  const requestedFiles = new Set(eligible.map((request) => `${request.key}.png`));
  if (requestedFiles.size === 0) return urls;

  try {
    const files = await downloadWorkspaceFilesByBasename(workspaceId, requestedFiles);
    const directory = fileURLToPath(
      new URL(`../../runs/evidence/${runId}/`, import.meta.url),
    );
    await mkdir(directory, { recursive: true });

    for (const request of eligible) {
      const basename = `${request.key}.png`;
      const contents = files.get(basename);
      if (!contents) continue;
      await writeFile(`${directory}/${basename}`, contents, { mode: 0o600 });
      urls.set(
        request.key,
        `http://localhost:${config.port}/api/evidence/${encodeURIComponent(runId)}/${encodeURIComponent(basename)}`,
      );
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[browser-use] checkout screenshot evidence unavailable: ${message}`);
  }

  return urls;
}

function withScreenshotEvidence(quote: CartQuote, screenshotUrl: string | undefined): CartQuote {
  if (!screenshotUrl) return quote;
  return CartQuoteSchema.parse({
    ...quote,
    evidence: { ...quote.evidence, screenshotUrl },
  });
}

export async function verifyCart(
  request: CartRequest,
  observer?: BrowserTaskObserver,
): Promise<CartQuote> {
  if (config.mode === "fixture") {
    return fixtureQuote(request);
  }

  try {
    const result = await runBrowserTask(
      buildCheckoutPrompt(request),
      CheckoutAgentOutputSchema,
      { retryTransient: false, observer },
    );
    const evidence = await persistCheckoutEvidence(
      [request],
      result.runId,
      result.workspaceId,
    );
    return withScreenshotEvidence(
      buildCartQuote(request, result.data, result.runId),
      evidence.get(request.key),
    );
  } catch (error) {
    return failedQuote(request, error);
  }
}

export async function verifySupplierCarts(
  requests: CartRequest[],
  observer?: BrowserTaskObserver,
): Promise<CartQuote[]> {
  if (requests.length === 0) return [];

  const supplierId = requests[0]!.supplier.id;
  if (requests.some((request) => request.supplier.id !== supplierId)) {
    throw new Error("Supplier checkout batches cannot mix suppliers");
  }

  if (config.mode === "fixture") {
    return Promise.all(requests.map((request) => verifyCart(request)));
  }

  try {
    const result = await runBrowserTask(
      buildBatchCheckoutPrompt(requests),
      CheckoutBatchAgentOutputSchema,
      { retryTransient: false, timeoutMs: 20 * 60_000, observer },
    );
    const evidence = await persistCheckoutEvidence(
      requests,
      result.runId,
      result.workspaceId,
    );

    return requests.map((request) => {
      const matches = result.data.quotes.filter((quote) => quote.cartKey === request.key);
      if (matches.length !== 1) {
        return failedQuote(
          request,
          new BrowserRunError(
            matches.length === 0
              ? `Checkout agent omitted cart ${request.key}`
              : `Checkout agent returned duplicate quotes for cart ${request.key}`,
            false,
            result.runId,
          ),
        );
      }

      const { cartKey: _cartKey, ...output } = matches[0]!;
      return withScreenshotEvidence(
        buildCartQuote(request, output, result.runId),
        evidence.get(request.key),
      );
    });
  } catch (error) {
    return requests.map((request) => failedQuote(request, error));
  }
}
