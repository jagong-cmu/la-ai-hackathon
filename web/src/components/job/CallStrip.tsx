"use client";
import { useState } from "react";
import type { JobEvent } from "@/api/client";
import { Phone } from "@/components/Icons";
import { mmss } from "@/lib/format";

export interface CallLine {
  speaker: string;
  text: string;
  at: number; // seconds from call start
}

/** The distributor call, from the job's events. */
export function callFromEvents(events: JobEvent[]) {
  const started = events.find((e) => e.type === "call_started");
  if (!started) return null;
  const t0 = new Date(started.ts).getTime();
  const lines: CallLine[] = events
    .filter((e) => e.type === "transcript_line")
    .map((e) => ({ speaker: String(e.data.speaker ?? ""), text: String(e.data.text ?? ""), at: (new Date(e.ts).getTime() - t0) / 1000 }));
  const ended = events.find((e) => e.type === "call_ended");
  const last = ended ?? events.filter((e) => e.laneId === "distributor").at(-1) ?? started;
  const offer = events.findLast((e) => e.type === "call_offer");
  const sms = events.findLast((e) => e.type === "message_sent");
  return {
    to: String(started.data.to ?? "Distributor"),
    startedAt: started.ts,
    duration: (new Date(last.ts).getTime() - t0) / 1000,
    ended: !!ended,
    lines,
    offerTotal: typeof offer?.data.total === "number" ? (offer.data.total as number) : null,
    sms: sms ? { text: String(sms.data.text ?? ""), ts: sms.ts } : null,
  };
}

export function Transcript({ lines, who }: { lines: CallLine[]; who: string }) {
  return (
    <dl className="transcript">
      {lines.map((l, i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt className="tnum">{l.speaker === "agent" ? "Agent" : who} · {mmss(l.at)}</dt>
          <dd>{l.text}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CallStrip({ call, who }: { call: NonNullable<ReturnType<typeof callFromEvents>>; who: string }) {
  const [open, setOpen] = useState(false);
  const last = call.lines.at(-1);
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="callstrip">
        <Phone />
        <span className="who tnum">{who} · {call.ended ? "call" : "on call"} · {mmss(call.duration)}</span>
        <q>{last?.text ?? "Ringing"}</q>
        <button className="btn btn-on-black sm" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Hide" : "Transcript"}
        </button>
      </div>
      {open && (
        <div className="card">
          <Transcript lines={call.lines} who={who} />
        </div>
      )}
    </div>
  );
}
