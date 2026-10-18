import { readFileSync } from "node:fs";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  CartQuoteSchema,
  DiscoveryResultSchema,
  MvpShoppingRequestSchema,
  ShoppingRequestSchema,
} from "../src/schemas.js";
import { clearJobs, createJob } from "../src/jobs/store.js";
import { app } from "../src/server.js";

function readFixture(name: string): unknown {
  const contents = readFileSync(new URL(`../src/fixtures/${name}`, import.meta.url), "utf8");
  return JSON.parse(contents);
}

describe("Cartwise API", () => {
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    await new Promise<void>((resolve) => {
      server = app.listen(0, "127.0.0.1", () => resolve());
    });

    const address = server.address();
    if (!address || typeof address === "string") {
      throw new Error("Test server did not expose a TCP address");
    }

    baseUrl = `http://127.0.0.1:${address.port}`;
  });

  afterAll(async () => {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    clearJobs();
  });

  it("returns fixture mode from the health endpoint", async () => {
    const response = await fetch(`${baseUrl}/api/health`);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, mode: "fixture" });
  });

  it("keeps all fixture files valid", () => {
    expect(() => MvpShoppingRequestSchema.parse(readFixture("request.json"))).not.toThrow();
    expect(() => DiscoveryResultSchema.array().parse(readFixture("discovery.json"))).not.toThrow();
    expect(() => CartQuoteSchema.array().length(6).parse(readFixture("quotes.json"))).not.toThrow();
  });

  it("accepts any positive number of products and suppliers", () => {
    const request = MvpShoppingRequestSchema.parse(readFixture("request.json"));
    const thirdItem = { ...request.items[0]!, id: "item-3" };
    const thirdSupplier = { ...request.suppliers[0]!, id: "supplier-3" };

    expect(() =>
      ShoppingRequestSchema.parse({
        ...request,
        items: [...request.items, thirdItem],
        suppliers: [...request.suppliers, thirdSupplier],
      }),
    ).not.toThrow();
    expect(() =>
      ShoppingRequestSchema.parse({
        ...request,
        items: Array.from({ length: 12 }, (_, index) => ({
          ...request.items[0]!,
          id: `item-${index + 1}`,
        })),
        suppliers: Array.from({ length: 10 }, (_, index) => ({
          ...request.suppliers[0]!,
          id: `supplier-${index + 1}`,
        })),
      }),
    ).not.toThrow();
  });

  it("rejects invalid requests and unapproved supplier URLs", async () => {
    const invalid = await fetch(`${baseUrl}/api/comparisons`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(invalid.status).toBe(400);

    const request = MvpShoppingRequestSchema.parse(readFixture("request.json"));
    request.suppliers[0]!.url = "http://127.0.0.1:8080";
    const unsafe = await fetch(`${baseUrl}/api/comparisons`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
    });
    expect(unsafe.status).toBe(400);

    const duplicate = MvpShoppingRequestSchema.parse(readFixture("request.json"));
    duplicate.suppliers[1]!.id = duplicate.suppliers[0]!.id;
    const duplicateResponse = await fetch(`${baseUrl}/api/comparisons`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(duplicate),
    });
    expect(duplicateResponse.status).toBe(400);
  });

  it("returns 404 for an unknown job", async () => {
    const response = await fetch(`${baseUrl}/api/comparisons/not-a-job`);
    expect(response.status).toBe(404);
  });

  it("cancels a queued comparison", async () => {
    const job = createJob();
    const response = await fetch(`${baseUrl}/api/comparisons/${job.id}/cancel`, {
      method: "POST",
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      id: job.id,
      status: "cancelled",
      message: "Comparison cancelled",
      error: null,
    });

    const saved = await fetch(`${baseUrl}/api/comparisons/${job.id}`);
    expect(await saved.json()).toMatchObject({ status: "cancelled" });
  });

  it("runs the complete fixture comparison as a background job", async () => {
    const response = await fetch(`${baseUrl}/api/comparisons`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(readFixture("request.json")),
    });
    expect(response.status).toBe(202);
    const started = (await response.json()) as { jobId: string; status: string };
    expect(started.status).toBe("queued");

    let job: Record<string, any> | null = null;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const current = await fetch(`${baseUrl}/api/comparisons/${started.jobId}`);
      expect(current.status).toBe(200);
      job = await current.json();
      if (job?.status === "complete" || job?.status === "failed") break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }

    expect(job?.status).toBe("complete");
    expect(job?.mode).toBe("fixture");
    expect(job?.progress).toEqual({ completed: 6, total: 6 });
    expect(job?.browserViews).toEqual([]);
    expect(job?.result).toMatchObject({
      lowestVerifiedTotalCents: 2500,
      bestSingleSupplierTotalCents: 3000,
      savingsCents: 500,
      scope: "all_feasible_verified",
    });
  });
});
