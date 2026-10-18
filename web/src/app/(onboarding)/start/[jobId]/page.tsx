"use client";
// Onboarding steps 3 and 4: watch the agents get quotes, then the best plan against the bills.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { money, type JobView } from "@/api/client";
import { ArrowRight } from "@/components/Icons";
import { BrowserPane } from "@/components/job/BrowserPane";
import { CallPanel, callFromEvents } from "@/components/job/CallStrip";
import { useJobEvents } from "@/hooks/useJobEvents";
import { cleanName, int, itemShort, shortName } from "@/lib/format";
import { supplierNames, supplierUrls, tileCols } from "@/lib/job";
import { pct, planSavings, pricedPlan } from "@/lib/savings";
import { Roll } from "@/components/Roll";
import { Claim, In, ONBOARDED_KEY, Steps } from "../../steps";

/** The best price so far, with a one-shot flash when it drops. The number is the static cue. */
function Best({ total }: { total: number | null }) {
  const [bump, setBump] = useState(false);
  const last = useRef(total);
  useEffect(() => {
    if (total == null || last.current === total) return;
    last.current = total;
    setBump(true);
    const t = setTimeout(() => setBump(false), 600);
    return () => clearTimeout(t);
  }, [total]);
  return (
    <div className={`ob-best${bump ? " bump" : ""}`} aria-live="polite">
      <span>Best so far</span>
      <b>{total != null ? <Roll text={money(total)} /> : "–"}</b>
    </div>
  );
}

function Result({ job }: { job: JobView }) {
  const names = supplierNames(job);
  const plan = pricedPlan(job);
  const sv = planSavings(job, plan);
  const saved = sv && sv.saved > 0.005 ? sv.saved : 0;
  const items = job.request.items.filter((i) => i.quantity > 0);

  if (!plan) {
    return (
      <main className="ob-stage">
        <In i={0}><Steps at={3} /></In>
        <Claim text="No prices this time" start={100} />
        <In i={5} className="ob-row">
          <Link href="/start" className="btn btn-primary has-icon">Try again <ArrowRight /></Link>
          <Link href={`/jobs/${job.jobId}`} className="btn btn-on-black">See each store</Link>
        </In>
      </main>
    );
  }

  return (
    <main className="ob-stage">
      <In i={0}><Steps at={3} /></In>
      <In i={1} as="p" className="ob-figure-label">Best plan</In>
      <In i={2} as="h1" className="ob-figure"><Roll text={money(plan.total)} fromZero /></In>
      <In i={3} as="p" className={`ob-figure-sub${saved ? "" : " flat"}`}>
        {sv ? (saved ? `${money(saved)} less than your bills · ${pct(sv.pct)}` : `Your bills: ${money(sv.paidBefore)}`) : "Total with shipping and tax"}
      </In>
      <In i={4} className="ob-device">
        <div className="ob-screen">
          <div style={{ overflowX: "auto" }}>
            <table className="tbl wide">
              <thead>
                <tr>
                  <th>Supply</th>
                  <th className="l">From</th>
                  <th>Your bills</th>
                  <th className="plan"><b>Your plan</b></th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, k) => {
                  const a = plan.assignments.find((x) => x.itemId === it.id);
                  const before = it.pastUnitPrice != null ? it.quantity * it.pastUnitPrice : null;
                  return (
                    <tr key={it.id} className="ob-in" style={{ "--d": `${500 + k * 100}ms` } as React.CSSProperties}>
                      <td>{itemShort(it.name)} × {int(it.quantity)}</td>
                      <td className="l ink-2">{a ? `${cleanName(names[a.supplierId] ?? a.supplierId)}${a.source === "web" ? "" : " · phone"}` : "–"}</td>
                      <td className="muted">{a && before != null ? money(before) : "–"}</td>
                      <td className={a ? "plan best" : "plan dim"}>{a ? money(a.lineCost) : "No price"}</td>
                    </tr>
                  );
                })}
                {sv && sv.extras >= 0.005 && (
                  <tr>
                    <td>Shipping and tax</td>
                    <td />
                    <td />
                    <td className="plan best">{money(sv.extras)}</td>
                  </tr>
                )}
              </tbody>
              <tfoot>
                <tr>
                  <td>Checkout total</td>
                  <td />
                  <td className="muted">{sv ? money(sv.paidBefore) : ""}</td>
                  <td className="plan">{money(plan.total)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </In>
      <In i={6} className="ob-row">
        <Link href={`/jobs/${job.jobId}/results`} className="btn btn-primary has-icon">Open best plan <ArrowRight /></Link>
        <Link href="/planner" className="btn btn-on-black">Open planner</Link>
      </In>
    </main>
  );
}

export default function OnboardingJobPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const s = useJobEvents(jobId);
  const job = s.job;
  const ready = !!job?.results;

  useEffect(() => {
    if (!ready) return;
    try {
      localStorage.setItem(ONBOARDED_KEY, "1");
    } catch {
      // Private mode: the flag is only a convenience.
    }
  }, [ready]);

  if (!job) {
    return (
      <main className="ob-stage">
        {s.error ? <p className="alert" role="alert">{s.error}</p> : <Steps at={2} />}
      </main>
    );
  }
  if (ready) return <Result job={job} />;

  const names = supplierNames(job);
  const urls = supplierUrls(job);
  const order = job.request.suppliers.map((x) => x.id);
  const lanes = Object.values(s.lanes).sort((a, b) => order.indexOf(a.supplierId) - order.indexOf(b.supplierId));
  const distributor = job.request.distributor ? shortName(job.request.distributor.name) : "Distributor";
  const tiles = Math.max(1, lanes.length) + (job.request.distributor ? 1 : 0);

  return (
    <main className="ob-stage wide">
      <div className="ob-work-head">
        <div>
          <In i={0}><Steps at={2} /></In>
          <In i={1} as="h1">Getting quotes</In>
        </div>
        <In i={2}><Best total={s.bestPlan?.total ?? null} /></In>
      </div>
      <In i={3} className="ob-device grid">
        <div className="tiles" style={{ "--tile-cols": tileCols(tiles) } as React.CSSProperties}>
          {lanes.map((l, k) => (
            <div key={l.laneId} className="ob-in" style={{ "--d": `${400 + k * 100}ms`, display: "flex", minHeight: 0 } as React.CSSProperties}>
              <BrowserPane lane={l} name={names[l.supplierId] ?? l.supplierId} siteUrl={urls[l.supplierId]} job={job} />
            </div>
          ))}
          {lanes.length === 0 && <div className="skeleton" />}
          {job.request.distributor && (
            <div className="ob-in" style={{ "--d": `${400 + lanes.length * 100}ms`, display: "flex", minHeight: 0 } as React.CSSProperties}>
              <CallPanel call={callFromEvents(s.events)} who={distributor} />
            </div>
          )}
        </div>
      </In>
    </main>
  );
}
