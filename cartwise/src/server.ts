import cors from "cors";
import express from "express";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";
import { cancelComparison, runComparison } from "./core/orchestrator.js";
import { createJob, getJob } from "./jobs/store.js";
import { ShoppingRequestSchema } from "./schemas.js";

function isApprovedSupplierUrl(value: string): boolean {
  const url = new URL(value);
  const hostname = url.hostname.toLowerCase();
  return (
    ["http:", "https:"].includes(url.protocol) &&
    !url.username &&
    !url.password &&
    config.approvedSupplierDomains.some(
      (domain) => hostname === domain || hostname.endsWith(`.${domain}`),
    )
  );
}

export function createApp() {
  const app = express();

  app.use(cors({ origin: config.webOrigin }));
  app.use(express.json());
  app.use(express.static(fileURLToPath(new URL("../public", import.meta.url))));

  app.get("/api/health", (_request, response) => {
    response.json({ ok: true, mode: config.mode });
  });

  app.get("/api/evidence/:runId/:file", async (request, response) => {
    const { runId, file } = request.params;
    if (
      !/^[a-zA-Z0-9-]{1,128}$/.test(runId) ||
      !/^cart-[a-f0-9]{64}\.png$/.test(file)
    ) {
      response.status(404).json({ error: "Evidence image not found" });
      return;
    }

    try {
      const path = fileURLToPath(
        new URL(`../runs/evidence/${runId}/${file}`, import.meta.url),
      );
      const image = await readFile(path);
      response.set("Cache-Control", "private, max-age=3600");
      response.type("png").send(image);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        response.status(404).json({ error: "Evidence image not found" });
        return;
      }
      throw error;
    }
  });

  app.post("/api/comparisons", (request, response) => {
    const parsed = ShoppingRequestSchema.safeParse(request.body);
    if (!parsed.success) {
      response.status(400).json({
        error: "Invalid shopping request",
        issues: parsed.error.issues,
      });
      return;
    }

    const rejectedSupplier = parsed.data.suppliers.find(
      (supplier) => !isApprovedSupplierUrl(supplier.url),
    );
    if (rejectedSupplier) {
      response.status(400).json({
        error: `Supplier URL is not approved: ${rejectedSupplier.url}`,
      });
      return;
    }

    const job = createJob();
    response.status(202).json({ jobId: job.id, status: job.status });
    void runComparison(parsed.data, job.id);
  });

  app.get("/api/comparisons/:id", (request, response) => {
    const job = getJob(request.params.id);
    if (!job) {
      response.status(404).json({ error: "Comparison job not found" });
      return;
    }
    response.json(job);
  });

  app.post("/api/comparisons/:id/cancel", async (request, response) => {
    const job = await cancelComparison(request.params.id);
    if (!job) {
      response.status(404).json({ error: "Comparison job not found" });
      return;
    }
    response.json(job);
  });

  app.use((error: unknown, _request: express.Request, response: express.Response, _next: express.NextFunction) => {
    const message = error instanceof Error ? error.message : "Invalid request";
    response.status(400).json({ error: message });
  });

  return app;
}

export const app = createApp();

if (process.env.NODE_ENV !== "test") {
  app.listen(config.port, () => {
    console.log(`Cartwise API listening on http://localhost:${config.port}`);
  });
}
