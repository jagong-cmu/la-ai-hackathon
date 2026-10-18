"use client";
import { useEffect, useRef } from "react";
import { money, type JobEvent } from "@/api/client";
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

/** The distributor call, open beside the browser agents: the live transcript, newest line in view. */
export function CallPanel({ call, who }: { call: ReturnType<typeof callFromEvents>; who: string }) {
  const end = useRef<HTMLDivElement>(null);
  const count = call?.lines.length ?? 0;
  useEffect(() => {
    const box = end.current?.parentElement;
    if (box) box.scrollTop = box.scrollHeight;
  }, [count]);
  const state = !call ? "Not called yet" : call.ended ? `Call ended · ${mmss(call.duration)}` : `On call · ${mmss(call.duration)}`;
  return (
    <section className="callpanel" aria-label={`Call to ${who}`}>
      <div className="callpanel-head">
        <Phone />
        <b>{who}</b>
        {call && !call.ended ? <span className="bp-live-tag"><i />{state}</span> : <span className="who tnum">{state}</span>}
      </div>
      <div className="callpanel-lines" aria-live="polite">
        {call && call.lines.length === 0 && <p className="muted-dark">Ringing</p>}
        {call?.lines.map((l, i) => (
          <div key={i} className={`cl${l.speaker === "agent" ? " agent" : ""}`}>
            <span className="tnum">{l.speaker === "agent" ? "Agent" : who} · {mmss(l.at)}</span>
            <p>{l.text}</p>
          </div>
        ))}
        <div ref={end} />
      </div>
      {call?.offerTotal != null && (
        <div className="callpanel-offer">
          <span>Phone offer</span>
          <b className="tnum">{money(call.offerTotal)}</b>
        </div>
      )}
    </section>
  );
}
