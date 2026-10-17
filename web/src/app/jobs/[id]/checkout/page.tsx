"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { apiUrl, money } from "@/api/client";
import { StatusChip } from "@/components/StatusChip";
import { useJobEvents } from "@/hooks/useJobEvents";

export default function CheckoutPage() {
  const { id } = useParams<{ id: string }>();
  const s = useJobEvents(id);
  const job = s.job;
  if (!job) return <p>Loading job...</p>;

  const cards = new Map((job.cards ?? []).map((c) => [c.cardId, c]));
  const handoffs = job.handoffs ?? [];

  return (
    <>
      <h1>Checkout handoff</h1>
      <p className="row">
        <span>Job {id} - state: <strong>{s.jobState}</strong></span>
        <Link href={`/jobs/${id}/results`}>Results</Link>
      </p>
      {handoffs.length === 0 && <p className="muted" aria-live="polite">No handoffs yet. They appear on handoff_ready events.</p>}
      <div className="grid">
        {handoffs.map((h) => {
          const card = h.cardId ? cards.get(h.cardId) : (job.cards ?? []).find((c) => c.supplierId === h.supplierId);
          const failed = h.status === "failed";
          return (
            <section key={h.supplierId} className={`card${failed ? " lane-failed" : ""}`} aria-label={`Handoff ${h.supplierId}`}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{h.supplierId}</strong>
                <StatusChip status={failed ? "failed" : h.status.startsWith("quoted") ? "quoted" : null} label={h.status} />
              </div>
              {h.reason && <p className={failed ? "error" : "muted"}>{h.reason}</p>}
              <div className="row">
                {h.liveUrl && <a href={h.liveUrl} target="_blank" rel="noreferrer">Live view</a>}
                {h.screenshotUrl && <a href={apiUrl(h.screenshotUrl)} target="_blank" rel="noreferrer">Last screenshot</a>}
              </div>
              {card && (
                <div className="card" style={{ marginTop: 8 }}>
                  <div className="row">
                    <strong>Card ****{card.last4}</strong>
                    {card.simulated !== false && <span className="chip neutral">simulated</span>}
                  </div>
                  <div>limit {money(card.limitUsd)} - locked to {card.merchantLock}</div>
                  <div className="muted">expires {card.expiresAt} - {card.status}</div>
                </div>
              )}
              {/* Placeholder: no order is placed from this UI. */}
              <button style={{ marginTop: 8 }} onClick={() => undefined} aria-describedby={`ph-${h.supplierId}`}>
                Place order
              </button>
              <span id={`ph-${h.supplierId}`} className="muted" style={{ fontSize: "0.8em" }}> (placeholder, does nothing)</span>
            </section>
          );
        })}
      </div>
    </>
  );
}
