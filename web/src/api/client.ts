// Small typed fetch client for the FastAPI backend.
// Types come from src/api/types.ts (generated from openapi.json; run `pnpm gen:api`).
import type { components } from "./types";

export type Schemas = components["schemas"];
export type SpendInsights = Schemas["SpendInsights"];
export type DiscoveryResult = Schemas["DiscoveryResult"];
export type ShoppingRequest = Schemas["ShoppingRequest"];
export type ShoppingItem = Schemas["ShoppingItem"];
export type JobView = Schemas["JobView"];
export type JobState = Schemas["JobState"];
export type JobResults = Schemas["JobResults"];
export type Plan = Schemas["Plan"];
export type LaneStatus = Schemas["LaneStatus"];
export type ApprovalDecision = Schemas["ApprovalDecision"];
export type PlannerView = Schemas["PlannerView"];
export type ProductForecast = Schemas["ProductForecast"];
export type HistoryPoint = Schemas["HistoryPoint"];
export type ForecastPoint = Schemas["ForecastPoint"];
export type SupplyPlan = Schemas["SupplyPlan"];
export type MissView = Schemas["MissView"];
export type ForecastAdjustment = Schemas["ForecastAdjustment"];
export type PlannerCheck = Schemas["PlannerCheck"];
export type PreflightReport = Schemas["PreflightReport"];
export type JobCreated = Schemas["JobCreated"];
export type ModeChange = Schemas["ModeChange"];
export type SpendingRules = Schemas["SpendingRules"];
export type BillLine = Schemas["BillLineView"];

/** One SSE event (docs/DATA_FLOW.md §8). Not in OpenAPI because it is streamed. */
export interface JobEvent {
  seq: number;
  jobId: string;
  type: string;
  ts: string;
  laneId: string | null;
  data: Record<string, unknown>;
}

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(detailText(detail) || `HTTP ${status}`);
  }
}

/** Human text for a FastAPI `detail` (string, `{text}`, `{code, ...}` or validation list). */
export function detailText(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && "text" in detail && typeof (detail as { text: unknown }).text === "string") {
    return (detail as { text: string }).text;
  }
  return JSON.stringify(detail);
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

/** Turn a backend-relative URL (for example /evidence/...) into an absolute one. */
export function apiUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
}

type Query = Record<string, string | number | boolean | undefined | null>;

async function request<T>(
  method: "GET" | "POST",
  path: string,
  opts: { body?: unknown; query?: Query; headers?: Record<string, string> } = {},
): Promise<T> {
  const url = new URL(API_BASE_URL + path);
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  }
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url, {
    method,
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    cache: "no-store",
  });
  const text = await res.text();
  let json: unknown = undefined;
  try {
    json = text ? JSON.parse(text) : undefined;
  } catch {
    json = text;
  }
  if (!res.ok) {
    const detail = json && typeof json === "object" && "detail" in json ? (json as { detail: unknown }).detail : json;
    throw new ApiError(res.status, detail);
  }
  return json as T;
}

const admin = (token: string) => ({ "X-Admin-Token": token });

export const api = {
  insights: (businessId?: string) => request<SpendInsights>("GET", "/insights", { query: { businessId } }),
  suppliers: (category?: string, zip?: string) =>
    request<DiscoveryResult>("GET", "/suppliers", { query: { category, zip } }),
  /** Supply lines of the past QuickBooks bills, oldest first. */
  bills: (itemId?: string) => request<BillLine[]>("GET", "/bills", { query: { itemId } }),
  shoppingDraft: (category?: string) => request<ShoppingRequest>("GET", "/shopping-list/draft", { query: { category } }),

  planner: () => request<PlannerView>("GET", "/planner"),
  plannerCheck: (autostart = false) => request<PlannerCheck>("POST", "/planner/check", { query: { autostart } }),
  ownerMessage: (text: string) =>
    request<ForecastAdjustment>("POST", "/planner/owner-messages", { body: { text } satisfies Schemas["OwnerMessage"] }),
  confirmAdjustment: (adjustmentId: string, accept: boolean) =>
    request<ForecastAdjustment>("POST", `/planner/adjustments/${encodeURIComponent(adjustmentId)}/confirm`, {
      query: { accept },
    }),
  answerMiss: (missId: string, cause: string) =>
    request<PlannerView>("POST", `/planner/misses/${encodeURIComponent(missId)}/answer`, {
      body: { cause } satisfies Schemas["MissAnswer"],
    }),

  /** POST /jobs. A new Idempotency-Key per call unless one is passed (pass the same key to retry safely). */
  createJob: (req: ShoppingRequest, idempotencyKey: string = crypto.randomUUID()) =>
    request<JobCreated>("POST", "/jobs", { body: req, headers: { "Idempotency-Key": idempotencyKey } }),
  jobs: (limit = 50) => request<JobView[]>("GET", "/jobs", { query: { limit } }),
  rules: () => request<SpendingRules>("GET", "/rules"),
  /** Plain-text evidence (fixture screenshots, call transcripts) served by the backend. */
  evidenceText: async (path: string) => {
    const res = await fetch(apiUrl(path)!, { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
  /** The events so far, without following the stream (SSE frames parsed here). */
  eventsOnce: async (jobId: string, after = 0): Promise<JobEvent[]> => {
    const res = await fetch(`${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/events?follow=false&after=${after}`, { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    const out: JobEvent[] = [];
    for (const frame of (await res.text()).split("\n\n")) {
      const data = frame.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (data) out.push(JSON.parse(data) as JobEvent);
    }
    return out;
  },
  job: (jobId: string) => request<JobView>("GET", `/jobs/${encodeURIComponent(jobId)}`),
  approve: (jobId: string, decision: ApprovalDecision) =>
    request<JobView>("POST", `/jobs/${encodeURIComponent(jobId)}/approve`, { body: decision }),
  eventsUrl: (jobId: string) => `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/events`,

  health: () => request<{ ok: boolean; runMode: string; modes: Record<string, string> }>("GET", "/health"),
  decisions: (token: string) => request<Record<string, unknown>[]>("GET", "/admin/decisions", { headers: admin(token) }),
  preflight: (apply = false) => request<PreflightReport>("GET", "/health/preflight", { query: { apply } }),
  getModes: (token: string) => request<Record<string, string>>("GET", "/admin/mode", { headers: admin(token) }),
  setMode: (token: string, change: ModeChange) =>
    request<Record<string, string>>("POST", "/admin/mode", { body: change, headers: admin(token) }),
  replay: (token: string, sourceJobId: string, speed = 1) =>
    request<JobCreated>("POST", `/admin/replay/${encodeURIComponent(sourceJobId)}`, {
      query: { speed },
      headers: admin(token),
    }),
};

export const TERMINAL_JOB_STATES: ReadonlySet<string> = new Set(["at_payment", "partial_handoff", "rejected", "failed"]);

export const money = (n: number | null | undefined) =>
  n == null ? "-" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });
