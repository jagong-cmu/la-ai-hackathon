"use client";
// Live job state: GET /jobs/{id} for the first state, then EventSource on /jobs/{id}/events.
// The browser's EventSource resends Last-Event-ID itself when it reconnects, so the
// backend resumes after the last seq we saw. We also drop any event with seq <= lastSeq.
import { useCallback, useEffect, useReducer, useRef } from "react";
import { api, TERMINAL_JOB_STATES, type JobEvent, type JobView, type LaneStatus } from "@/api/client";

/** Every event type the backend emits (docs/DATA_FLOW.md §8 + backend code). */
export const EVENT_TYPES = [
  "job_started", "job_state", "deadline_hit", "results_ready", "results_updated", "approved", "rejected", "job_failed",
  "discovery_started", "discovery_done", "spec_ready",
  "lane_state", "page_state", "guard_stop", "offers_ready", "supplier_cart_ready", "subset_total", "screenshot",
  "checkout_filled", "plans_updated",
  "message_sent", "reply_received", "call_started", "call_skipped", "transcript_line", "call_offer", "call_ended",
  "quote_received",
  "rules_checked", "card_minted", "handoff_ready",
  "dependency_degraded", "replay_started",
] as const;

const REFETCH_ON = new Set(["plans_updated", "offers_ready", "results_ready", "results_updated", "handoff_ready", "job_state", "approved", "rejected"]);

export interface LaneTile {
  laneId: string;
  supplierId: string;
  state: string;
  reason: string | null;
  mode?: string;
  liveUrl: string | null;
  lastScreenshotUrl: string | null;
  pageState?: string;
  pageUrl?: string;
  cartTotal?: number;
  subsetTotal?: number;
  /** The most recent cart total this lane read (full cart or a subset). */
  lastTotal?: number;
  totalsCheck?: string;
  updatedAt?: string;
}

export interface TranscriptEntry {
  seq: number;
  ts: string;
  kind: "sms_out" | "sms_in" | "call" | "offer" | "info";
  speaker?: string;
  text: string;
}

export interface BestPlan {
  planId: string | null;
  total: number | null;
  status: string | null;
}

export interface JobEventsState {
  job: JobView | null;
  jobState: string | null;
  lanes: Record<string, LaneTile>;
  bestPlan: BestPlan | null;
  transcript: TranscriptEntry[];
  events: JobEvent[];
  lastSeq: number;
  connection: "idle" | "connecting" | "open" | "closed" | "error";
  error: string | null;
}

type Action =
  | { type: "job"; job: JobView }
  | { type: "event"; event: JobEvent }
  | { type: "connection"; connection: JobEventsState["connection"] }
  | { type: "error"; error: string | null };

const initial: JobEventsState = {
  job: null, jobState: null, lanes: {}, bestPlan: null, transcript: [], events: [], lastSeq: 0,
  connection: "idle", error: null,
};

function laneFromStatus(l: LaneStatus, prev?: LaneTile): LaneTile {
  return {
    ...prev,
    laneId: l.laneId,
    supplierId: l.supplierId,
    mode: l.mode,
    state: l.state ?? prev?.state ?? "queued",
    reason: l.reason ?? null,
    liveUrl: l.liveUrl ?? prev?.liveUrl ?? null,
    lastScreenshotUrl: l.lastScreenshotUrl ?? prev?.lastScreenshotUrl ?? null,
    updatedAt: l.updatedAt,
  };
}

const str = (v: unknown) => (v == null ? "" : String(v));
const num = (v: unknown) => (typeof v === "number" ? v : undefined);

function applyEvent(s: JobEventsState, e: JobEvent): JobEventsState {
  const d = e.data ?? {};
  const next: JobEventsState = { ...s, lastSeq: e.seq, events: [...s.events, e] };

  // Lane tiles
  if (e.laneId && e.laneId !== "distributor") {
    const prev: LaneTile = s.lanes[e.laneId] ?? {
      laneId: e.laneId, supplierId: e.laneId.replace(/^lane_/, ""), state: "queued", reason: null,
      liveUrl: null, lastScreenshotUrl: null,
    };
    let lane: LaneTile | null = null;
    switch (e.type) {
      case "lane_state":
        lane = { ...prev, state: str(d.state) || prev.state, reason: (d.reason as string) ?? null };
        if (typeof d.liveUrl === "string") lane.liveUrl = d.liveUrl;
        break;
      case "guard_stop":
        lane = { ...prev, reason: str(d.reason) || prev.reason };
        break;
      case "screenshot":
        lane = { ...prev, lastScreenshotUrl: str(d.url) || prev.lastScreenshotUrl };
        break;
      case "page_state":
        lane = { ...prev, pageState: str(d.state), pageUrl: str(d.url) || prev.pageUrl };
        break;
      case "supplier_cart_ready":
        lane = { ...prev, cartTotal: num(d.total), lastTotal: num(d.total) ?? prev.lastTotal, totalsCheck: str(d.totalsCheck) || undefined };
        break;
      case "subset_total":
        lane = { ...prev, subsetTotal: num(d.total), lastTotal: num(d.total) ?? prev.lastTotal };
        break;
      case "handoff_ready":
        lane = { ...prev, liveUrl: (d.liveUrl as string) ?? prev.liveUrl };
        break;
    }
    if (lane) next.lanes = { ...s.lanes, [e.laneId]: { ...lane, updatedAt: e.ts } };
  }

  // Best plan so far
  if (e.type === "plans_updated") {
    next.bestPlan = {
      planId: (d.bestPlanId as string) ?? null,
      total: num(d.bestTotal) ?? null,
      status: (d.bestStatus as string) ?? (d.status as string) ?? null,
    };
  }

  // Distributor panel (SMS + call)
  const add = (entry: Omit<TranscriptEntry, "seq" | "ts">) => {
    next.transcript = [...s.transcript, { seq: e.seq, ts: e.ts, ...entry }];
  };
  switch (e.type) {
    case "message_sent":
      add({ kind: "sms_out", speaker: "agent", text: str(d.text) });
      break;
    case "reply_received":
      add({ kind: "sms_in", speaker: "distributor", text: d.text ? str(d.text) : `(reply: ${str(d.intent)})` });
      break;
    case "call_started":
      add({ kind: "info", text: `Call started${d.disclosure ? ` (${str(d.disclosure)})` : ""}` });
      break;
    case "call_skipped":
      add({ kind: "info", text: `Call skipped: ${str(d.reason)}` });
      break;
    case "transcript_line":
      add({ kind: "call", speaker: str(d.speaker), text: str(d.text) });
      break;
    case "call_offer":
      add({
        kind: "offer",
        text: `Offer ${d.total != null ? `$${num(d.total)?.toFixed(2)}` : d.price != null ? `$${str(d.price)}` : ""}` +
          `${d.p != null ? ` (p=${Number(d.p).toFixed(2)})` : ""}${d.beats === true ? " - beats best plan" : ""}`,
      });
      break;
    case "call_ended":
      add({ kind: "info", text: `Call ended${d.outcome ? `: ${str(d.outcome)}` : ""}` });
      break;
    case "quote_received":
      add({ kind: "info", text: `Quote received (${str(d.source)})` });
      break;
  }

  if (e.type === "job_state" && typeof d.state === "string") next.jobState = d.state;
  return next;
}

function reducer(s: JobEventsState, a: Action): JobEventsState {
  switch (a.type) {
    case "job": {
      const lanes = { ...s.lanes };
      for (const l of a.job.lanes ?? []) lanes[l.laneId] = laneFromStatus(l, lanes[l.laneId]);
      return { ...s, job: a.job, jobState: a.job.state, lanes };
    }
    case "event":
      if (a.event.seq <= s.lastSeq) return s; // duplicate after a reconnect
      return applyEvent(s, a.event);
    case "connection":
      return { ...s, connection: a.connection };
    case "error":
      return { ...s, error: a.error };
  }
}

export function useJobEvents(jobId: string | undefined) {
  const [state, dispatch] = useReducer(reducer, initial);
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refetch = useCallback(async () => {
    if (!jobId) return;
    try {
      dispatch({ type: "job", job: await api.job(jobId) });
    } catch (err) {
      dispatch({ type: "error", error: err instanceof Error ? err.message : String(err) });
    }
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    let closed = false;
    void refetch();
    dispatch({ type: "connection", connection: "connecting" });
    const es = new EventSource(api.eventsUrl(jobId));

    const scheduleRefetch = () => {
      if (refetchTimer.current) clearTimeout(refetchTimer.current);
      refetchTimer.current = setTimeout(() => void refetch(), 150);
    };

    const onEvent = (msg: MessageEvent<string>) => {
      let ev: JobEvent;
      try {
        ev = JSON.parse(msg.data) as JobEvent;
      } catch {
        return;
      }
      dispatch({ type: "event", event: ev });
      if (REFETCH_ON.has(ev.type)) scheduleRefetch();
      if (ev.type === "job_state" && TERMINAL_JOB_STATES.has(String(ev.data?.state))) {
        // Final state: the server closes the stream. Stop the browser from reconnecting.
        closed = true;
        es.close();
        dispatch({ type: "connection", connection: "closed" });
      }
    };

    for (const t of EVENT_TYPES) es.addEventListener(t, onEvent as EventListener);
    es.onmessage = onEvent; // events without an `event:` field
    es.onopen = () => dispatch({ type: "connection", connection: "open" });
    es.onerror = () => {
      if (closed) return;
      // EventSource reconnects by itself (server sends retry: 2000) with Last-Event-ID.
      dispatch({ type: "connection", connection: es.readyState === EventSource.CLOSED ? "error" : "connecting" });
    };

    return () => {
      closed = true;
      es.close();
      if (refetchTimer.current) clearTimeout(refetchTimer.current);
    };
  }, [jobId, refetch]);

  return { ...state, refetch };
}
