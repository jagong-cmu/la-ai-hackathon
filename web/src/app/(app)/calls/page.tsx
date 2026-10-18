"use client";
import { useEffect, useState } from "react";
import { api, errorText, money, type JobEvent, type JobView } from "@/api/client";
import { ErrorNote, Loading, PageHead } from "@/components/PageHead";
import { Status } from "@/components/Status";
import { callFromEvents, Transcript } from "@/components/job/CallStrip";
import { clock, mmss, shortName, tsDay, cleanName } from "@/lib/format";

type Call = NonNullable<ReturnType<typeof callFromEvents>> & { job: JobView };

export default function CallsPage() {
  const [calls, setCalls] = useState<Call[] | null>(null);
  const [open, setOpen] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const jobs = await api.jobs(30);
      const withCalls = await Promise.all(
        jobs.map(async (job) => {
          const events: JobEvent[] = await api.eventsOnce(job.jobId).catch(() => []);
          const call = callFromEvents(events);
          return call ? { ...call, job } : null;
        }),
      );
      setCalls(withCalls.filter((c): c is Call => c !== null));
    })().catch((e) => setError(errorText(e)));
  }, []);

  if (!calls) return error ? <ErrorNote error={error} /> : <Loading label="Loading calls" />;
  const c = calls[Math.min(open, calls.length - 1)];

  return (
    <>
      <PageHead
        title="Calls"
      />
      {calls.length === 0 ? (
        <div className="card empty">No calls yet</div>
      ) : (
        <div className="cols" style={{ "--cols": "minmax(0, 320px) minmax(0, 1fr)" } as React.CSSProperties}>
          <div className="tbl-wrap">
            <table className="tbl">
              <tbody>
                {calls.map((x, i) => (
                  <tr key={x.job.jobId} className="click" onClick={() => setOpen(i)} style={i === open ? { background: "var(--color-surface)" } : undefined}>
                    <td>
                      <button
                        style={{ all: "unset", cursor: "pointer", fontWeight: i === open ? 600 : 400 }}
                        aria-pressed={i === open}
                        onClick={() => setOpen(i)}
                      >
                        {cleanName(x.to)}
                      </button>
                      <span className="sub">
                        {x.offerTotal != null ? (
                          <Status tone="quoted">{tsDay(x.startedAt)} · {mmss(x.duration)} · quote {money(x.offerTotal)}</Status>
                        ) : (
                          `${tsDay(x.startedAt)} · ${mmss(x.duration)} · no offer`
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {c && (
            <section className="card" aria-label="Call">
              <div style={{ padding: "20px 20px 16px", borderBottom: "1px solid var(--color-line)" }}>
                <p style={{ fontWeight: 600, fontSize: 18, lineHeight: "24px" }}>
                  {cleanName(c.to)} · {tsDay(c.startedAt)}, {clock(c.startedAt)}
                </p>
                <p className="small ink-2 tnum" style={{ marginTop: 4 }}>{mmss(c.duration)} · AI disclosed</p>
              </div>
              <Transcript lines={c.lines} who={shortName(c.to)} />
              {(c.offerTotal != null || c.sms) && (
                <div style={{ padding: 20, borderTop: "1px solid var(--color-line)", display: "grid", gap: 16 }}>
                  {c.offerTotal != null && (
                    <div className="panel"><Status tone="quoted"><span className="strong" style={{ color: "var(--color-ink)" }}>{money(c.offerTotal)} quoted</span></Status></div>
                  )}
                  {c.sms && (
                    <div style={{ display: "grid", gap: 8 }}>
                      <span className="small ink-2">Text sent after the call · {clock(c.sms.ts)}</span>
                      <p className="panel" style={{ maxWidth: 520 }}>{c.sms.text}</p>
                    </div>
                  )}
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </>
  );
}
