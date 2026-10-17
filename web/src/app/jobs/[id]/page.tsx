"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { apiUrl, money } from "@/api/client";
import { StatusChip } from "@/components/StatusChip";
import { useJobEvents, type LaneTile } from "@/hooks/useJobEvents";

const FAILED = new Set(["failed", "blocked"]);

function Lane({ lane }: { lane: LaneTile }) {
  const failed = FAILED.has(lane.state);
  return (
    <section className={`card${failed ? " lane-failed" : ""}`} aria-label={`Lane ${lane.supplierId}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{lane.supplierId}</strong>
        <StatusChip status={failed ? "failed" : null} label={lane.state} />
      </div>
      {lane.mode && <div className="muted">mode: {lane.mode}</div>}
      {lane.reason && <div className={failed ? "error" : "muted"}>reason: {lane.reason}</div>}
      {lane.pageState && <div className="muted">page: {lane.pageState}</div>}
      {lane.cartTotal != null && (
        <div>cart {money(lane.cartTotal)} {lane.totalsCheck && <span className="muted">({lane.totalsCheck})</span>}</div>
      )}
      <div className="row">
        {lane.liveUrl && <a href={lane.liveUrl} target="_blank" rel="noreferrer">Live view</a>}
        {lane.lastScreenshotUrl && <a href={apiUrl(lane.lastScreenshotUrl)} target="_blank" rel="noreferrer">Last screenshot</a>}
      </div>
    </section>
  );
}

export default function WorkingPage() {
  const { id } = useParams<{ id: string }>();
  const s = useJobEvents(id);
  const job = s.job;
  const lanes = Object.values(s.lanes).sort((a, b) => a.supplierId.localeCompare(b.supplierId));

  return (
    <>
      <h1>Job {id}</h1>
      <p className="row">
        <span>State: <strong>{s.jobState ?? "..."}</strong></span>
        <span className="muted">stream: {s.connection} (seq {s.lastSeq})</span>
        {job?.replayOf && <span className="disclosure">Replay of {job.replayOf}</span>}
      </p>
      {s.error && <p className="error" role="alert">{s.error}</p>}
      <p className="row">
        {job?.results && <Link href={`/jobs/${id}/results`}>Results</Link>}
        {(job?.handoffs?.length ?? 0) > 0 && <Link href={`/jobs/${id}/checkout`}>Checkout</Link>}
      </p>

      <h2>Best plan so far</h2>
      <div aria-live="polite">
        {s.bestPlan?.planId ? (
          <p>
            {s.bestPlan.planId}: <strong>{money(s.bestPlan.total)}</strong> <StatusChip status={s.bestPlan.status} />
          </p>
        ) : (
          <p className="muted">No plan yet.</p>
        )}
      </div>

      <h2>Supplier lanes</h2>
      <div className="grid">
        {lanes.map((l) => <Lane key={l.laneId} lane={l} />)}
        {lanes.length === 0 && <p className="muted">No lanes yet.</p>}
      </div>

      <h2>Distributor (SMS and call)</h2>
      <div className="transcript" aria-live="polite">
        {s.transcript.length === 0 && <p className="muted">No messages yet.</p>}
        {s.transcript.map((t) => (
          <p key={t.seq} style={{ margin: "2px 0" }}>
            <span className="muted">[{t.kind}]</span> {t.speaker && <strong>{t.speaker}: </strong>}
            {t.text}
          </p>
        ))}
      </div>

      <details style={{ marginTop: 16 }}>
        <summary>Event log ({s.events.length})</summary>
        <ol style={{ fontFamily: "monospace", fontSize: "0.8em" }}>
          {s.events.map((e) => (
            <li key={e.seq} value={e.seq}>
              {e.type} {e.laneId ? `[${e.laneId}]` : ""} {JSON.stringify(e.data)}
            </li>
          ))}
        </ol>
      </details>
    </>
  );
}
