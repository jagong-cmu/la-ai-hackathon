import { randomUUID } from "node:crypto";
import { config } from "../config.js";
import { ComparisonJobSchema } from "../schemas.js";
import type { ComparisonJob } from "../types.js";

const jobs = new Map<string, ComparisonJob>();

export function createJob(): ComparisonJob {
  const job = ComparisonJobSchema.parse({
    id: randomUUID(),
    status: "queued",
    mode: config.mode,
    message: "Comparison queued",
    progress: { completed: 0, total: 0 },
    browserViews: [],
    result: null,
    error: null,
  });
  jobs.set(job.id, job);
  return structuredClone(job);
}

export function getJob(id: string): ComparisonJob | null {
  const job = jobs.get(id);
  return job ? structuredClone(job) : null;
}

export function updateJob(id: string, patch: Partial<ComparisonJob>): void {
  const current = jobs.get(id);
  if (!current) {
    throw new Error(`Unknown comparison job: ${id}`);
  }
  const updated = ComparisonJobSchema.parse({ ...current, ...patch, id: current.id });
  jobs.set(id, updated);
}

export function clearJobs(): void {
  jobs.clear();
}
